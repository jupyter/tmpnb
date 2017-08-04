import errno
import json
import os
import random
import string
import socket

from concurrent.futures import ThreadPoolExecutor
from collections import deque, namedtuple
from datetime import datetime, timedelta
from tornado import gen
from tornado import ioloop
from tornado.log import app_log
from tornado.httpclient import HTTPRequest, HTTPError, AsyncHTTPClient
from tornado.httputil import url_concat

import pytz
import re
import dockworker

AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")
import logging
logging.getLogger("tornado.curl_httpclient").setLevel(logging.INFO)

_date_fmt = '%Y-%m-%dT%H:%M:%S.%fZ'

def sample_with_replacement(a, size):
    '''Get a random path. If Python had sampling with replacement built in,
    I would use that. The other alternative is numpy.random.choice, but
    numpy is overkill for this tiny bit of random pathing.'''
    return "".join([random.SystemRandom().choice(a) for x in range(size)])


def new_user(size):
    return sample_with_replacement(string.ascii_letters + string.digits, size)


class PooledContainer(object):
    def __init__(self, id, path, token=''):
        self.id = id
        self.path = path
        self.token = token

    def __repr__(self):
        return 'PooledContainer(id=%s, path=%s)' % (self.id, self.path)

class EmptyPoolError(Exception):
    '''Exception raised when a container is requested from an empty pool.'''

    pass


class SpawnPool():
    '''Manage a pool of precreated Docker containers.'''

    def __init__(self,
                 proxy_endpoint,
                 proxy_token,
                 spawner,
                 container_config,
                 capacity,
                 max_idle,
                 max_age,
                 pool_name,
                 user_length,
                 static_files=None,
                 static_dump_path=os.path.join(os.path.dirname(__file__),
                                               "static")):
        '''Create a new, empty spawn pool, with nothing preallocated.'''

        self.spawner = spawner
        self.container_config = container_config
        self.capacity = capacity
        self.max_idle = max_idle
        self.max_age = max_age

        self.pool_name = pool_name
        self.container_name_pattern = re.compile('tmp\.([^.]+)\.(.+)\Z')

        self.proxy_endpoint = proxy_endpoint
        self.proxy_token = proxy_token

        self.user_length = user_length

        self.available = deque()
        self.started = {}

        self.static_files = static_files
        self.static_dump_path = static_dump_path

        self._heart_beating = False

    def acquire(self):
        '''Acquire a preallocated container and returns its user path.

        An EmptyPoolError is raised if no containers are ready.'''

        if not self.available:
            raise EmptyPoolError()

        container = self.available.pop()
        # signal start on acquisition
        self.started[container.id] = datetime.utcnow()
        return container

    @gen.coroutine
    def adhoc(self, user):
        '''Launch a container with a fixed path by taking the place of an existing container from
        the pool.'''

        to_release = self.acquire()
        app_log.debug("Discarding container [%s] to create an ad-hoc replacement.", to_release)
        yield self.release(to_release, False)

        launched = yield self._launch_container(user=user, enpool=False)
        self.started[launched.id] = datetime.utcnow()
        raise gen.Return(launched)

    @gen.coroutine
    def release(self, container, replace_if_room=True):
        '''Shut down a container and delete its proxy entry.

        Destroy the container in an orderly fashion. If requested and capacity is remaining, create
        a new one to take its place.'''

        try:
            app_log.info("Releasing container [%s].", container)
            self.started.pop(container.id, None)
            yield [
                self.spawner.shutdown_notebook_server(container.id),
                self._proxy_remove(container.path)
            ]
            app_log.debug("Container [%s] has been released.", container)
        except Exception as e:
            app_log.error("Unable to release container [%s]: %s", container, e)
            return

        if replace_if_room:
            running = yield self.spawner.list_notebook_servers(self.container_name_pattern, all=False)
            if len(running) + 1 <= self.capacity:
                app_log.debug("Launching a replacement container.")
                yield self._launch_container()
            else:
                app_log.info("Declining to launch a new container because [%i] containers are" +
                             " already running, and the capacity is [%i].",
                             len(running), self.capacity)

    @gen.coroutine
    def cleanout(self):
        '''Completely cleanout containers that are part of this pool.'''
        app_log.info("Performing initial pool cleanup")

        containers = yield self.spawner.list_notebook_servers(self.container_name_pattern, all=True)
        for container in containers:
            try:
                app_log.debug("Clearing old container [%s] from pool", container['Id'])
                yield self.spawner.shutdown_notebook_server(container['Id'])
            except Exception as e:
                app_log.warn(e)

    @gen.coroutine
    def drain(self):
        '''
        Completely cleanout all available containers in the pool and immediately
        schedule their replacement. Useful for refilling the pool with a new
        container image while leaving in-use containers untouched. Returns the
        number of containers drained.
        '''
        app_log.info("Draining available containers from pool")
        tasks = []
        while 1:
            try:
                pooled = self.acquire()
                app_log.debug("Releasing container [%s] to drain the pool.", pooled.id)
                tasks.append(self.release(pooled, replace_if_room=False))
            except EmptyPoolError:
                # No more free containers left to acquire
                break
        yield tasks
        raise gen.Return(len(tasks))

    @gen.coroutine
    def heartbeat(self):
        '''Examine the pool for any missing, stopped, or idle containers, and replace them.

        A container is considered "used" if it isn't still present in the pool. If no max_age is
        specified, an hour is used.'''

        if self._heart_beating:
            app_log.debug("Previous heartbeat is still active. Skipping this one.")
            return
        try:
            self._heart_beating = True

            app_log.debug("Heartbeat begun. Measuring current state.")

            diagnosis = Diagnosis(self.max_idle,
                                  self.max_age,
                                  self.spawner,
                                  self.container_name_pattern,
                                  self.proxy_endpoint,
                                  self.proxy_token,
                                  self.started,
                                  )
            yield diagnosis.observe()

            tasks = []

            for id in diagnosis.stopped_container_ids:
                app_log.debug("Removing stopped container [%s].", id)
                tasks.append(self.spawner.shutdown_notebook_server(id, alive=False))

            for path, id in diagnosis.zombie_routes:
                app_log.debug("Removing zombie route [%s].", path)
                tasks.append(self._proxy_remove(path))

            unpooled_stale_routes = [(path, id) for path, id in diagnosis.stale_routes
                                        if id not in self._pooled_ids()]
            for path, id in unpooled_stale_routes:
                app_log.debug("Replacing stale route [%s] and container [%s].", path, id)
                container = PooledContainer(path=path, id=id, token='')
                tasks.append(self.release(container, replace_if_room=True))

            # Normalize the container count to its initial capacity by scheduling deletions if we're
            # over or scheduling launches if we're under.
            current = len(diagnosis.living_container_ids)
            under = range(current, self.capacity)
            over = range(self.capacity, current)

            if under:
                app_log.info("Launching [%i] new containers to populate the pool.", len(under))
            for i in under:
                tasks.append(self._launch_container())

            if over:
                app_log.info("Removing [%i] containers to diminish the pool.", len(over))
            for i in over:
                try:
                    pooled = self.acquire()
                    app_log.info("Releasing container [%s] to shrink the pool.", pooled.id)
                    tasks.append(self.release(pooled, False))
                except EmptyPoolError:
                    app_log.warning("Unable to shrink: pool is diminished, all containers in use.")
                    break

            yield tasks

            # Summarize any actions taken to the log.
            def summarize(message, list):
                if list:
                    app_log.info(message, len(list))
            summarize("Removed [%i] stopped containers.", diagnosis.stopped_container_ids)
            summarize("Removed [%i] zombie routes.", diagnosis.zombie_routes)
            summarize("Replaced [%i] stale containers.", unpooled_stale_routes)
            summarize("Launched [%i] new containers.", under)
            summarize("Removed [%i] excess containers from the pool.", over)

            app_log.debug("Heartbeat complete. The pool now includes [%i] containers.",
                          len(self.available))
        finally:
            self._heart_beating = False

    @gen.coroutine
    def _launch_container(self, user=None, enpool=True):
        '''Launch a new notebook server in a fresh container, register it with the proxy, and
        add it to the pool.'''

        if user is None:
            user = new_user(self.user_length)

        path = "/user/%s/" % user

        # This must match self.container_name_pattern or Bad Things will happen.
        # You don't want Bad Things to happen, do you?
        container_name = 'tmp.{}.{}'.format(self.pool_name, user)
        if not self.container_name_pattern.match(container_name):
            raise Exception("[{}] does not match [{}]!".format(container_name,
                self.container_name_pattern.pattern))

        app_log.debug("Launching new notebook server [%s] at path [%s].",
                container_name, path)
        create_result = yield self.spawner.create_notebook_server(base_path=path,
                                                                  container_name=container_name,
                                                                  container_config=self.container_config)
        container_id, host_ip, host_port, token = create_result
        app_log.debug("Created notebook server [%s] for path [%s] at [%s:%s]", container_name, path, host_ip, host_port)

        # Wait for the server to launch within the container before adding it to the pool or
        # serving it to a user.
        yield self._wait_for_server(host_ip, host_port, path)

        http_client = AsyncHTTPClient()
        headers = {"Authorization": "token {}".format(self.proxy_token)}

        proxy_endpoint = "{}/api/routes{}".format(self.proxy_endpoint, path)
        body = json.dumps({
            "target": "http://{}:{}".format(host_ip, host_port),
            "container_id": container_id,
        })

        app_log.debug("Proxying path [%s] to port [%s].", path, host_port)
        req = HTTPRequest(proxy_endpoint,
                          method="POST",
                          headers=headers,
                          body=body)
        try:
            yield http_client.fetch(req)
            app_log.info("Proxied path [%s] to port [%s].", path, host_port)
        except HTTPError as e:
            app_log.error("Failed to create proxy route to [%s]: %s", path, e)

        container = PooledContainer(id=container_id, path=path, token=token)
        if enpool:
            app_log.info("Adding container [%s] to the pool.", container)
            self.available.append(container)

        raise gen.Return(container)

    @gen.coroutine
    def _wait_for_server(self, ip, port, path, timeout=10, wait_time=0.2):
        '''Wait for a server to show up within a newly launched container.'''

        app_log.info("Waiting for a container to launch at [%s:%s].", ip, port)
        loop = ioloop.IOLoop.current()
        tic = loop.time()

        # Docker starts listening on a socket before the container is fully launched. Wait for that,
        # first.
        while loop.time() - tic < timeout:
            try:
                socket.create_connection((ip, port))
            except socket.error as e:
                app_log.warn("Socket error on boot: %s", e)
                if e.errno != errno.ECONNREFUSED:
                    app_log.warn("Error attempting to connect to [%s:%i]: %s",
                                 ip, port, e)
                yield gen.Task(loop.add_timeout, loop.time() + wait_time)
            else:
                break

        # Fudge factor of IPython notebook bootup.
        # TODO: Implement a webhook in IPython proper to call out when the
        # notebook server is booted.
        yield gen.Task(loop.add_timeout, loop.time() + .5)

        # Now, make sure that we can reach the Notebook server.
        http_client = AsyncHTTPClient()
        req = HTTPRequest("http://{}:{}{}".format(ip, port, path))

        while loop.time() - tic < timeout:
            try:
                yield http_client.fetch(req)
            except HTTPError as http_error:
                code = http_error.code
                app_log.info("Booting server at [%s], getting HTTP status [%s]", path, code)
                yield gen.Task(loop.add_timeout, loop.time() + wait_time)
            else:
                break

        app_log.info("Server [%s] at address [%s:%s] has booted! Have at it.",
                     path, ip, port)

    def _pooled_ids(self):
        '''Build a set of container IDs that are currently waiting in the pool.'''

        return set(container.id for container in self.available)

    @gen.coroutine
    def _proxy_remove(self, path):
        '''Remove a path from the proxy.'''

        url = "{}/api/routes/{}".format(self.proxy_endpoint, path.lstrip('/'))
        headers = {"Authorization": "token {}".format(self.proxy_token)}
        req = HTTPRequest(url, method="DELETE", headers=headers)
        http_client = AsyncHTTPClient()

        try:
            yield http_client.fetch(req)
        except HTTPError as e:
            app_log.error("Failed to delete route [%s]: %s", path, e)

    @gen.coroutine
    def copy_static(self):
        if(self.static_files is None):
            raise Exception("static_files must be set in order to dump them")

        container = self.available[0]

        app_log.info("Extracting static files from container {}".format(container.id))

        tarball = yield self.spawner.copy_files(container.id, self.static_files)

        tar = open(os.path.join(self.static_dump_path, "static.tar"), "wb")
        tar.write(tarball.data)
        tar.close()

        app_log.debug("Static files extracted")

class Diagnosis():
    '''Collect and organize information to self-heal a SpawnPool.

    Measure the current state of Docker and the proxy routes and scan for anomalies so the pool can
    correct them. This includes zombie containers, containers that are running but not routed in the
    proxy, proxy routes that exist without a corresponding container, or other strange conditions.'''

    def __init__(self, cull_idle, cull_max_age, spawner, name_pattern, proxy_endpoint, proxy_token, started):
        self.spawner = spawner
        self.name_pattern = name_pattern
        self.proxy_endpoint = proxy_endpoint
        self.proxy_token = proxy_token
        self.cull_idle = cull_idle
        self.cull_max_age = cull_max_age
        self.started = started

    @gen.coroutine
    def observe(self):
        '''Collect Ground Truth of what's actually running from Docker and the proxy.'''

        results = yield {
            "docker": self.spawner.list_notebook_servers(self.name_pattern, all=True),
            "proxy": self._proxy_routes()
        }

        self.container_ids = set()
        self.living_container_ids = []
        self.stopped_container_ids = []
        self.zombie_container_ids = []

        self.routes = set()
        self.live_routes = []
        self.stale_routes = []
        self.zombie_routes = []

        # Sort Docker results into living and dead containers.
        for container in results["docker"]:
            id = container['Id']
            self.container_ids.add(id)
            if container['Status'].startswith('Up'):
                self.living_container_ids.append(id)
            else:
                self.stopped_container_ids.append(id)

        now = datetime.utcnow()
        idle_cutoff = now - self.cull_idle
        started_cutoff = now - self.cull_max_age

        # Sort proxy routes into living, stale, and zombie routes.
        living_set = set(self.living_container_ids)
        for path, route in results["proxy"].items():
            last_activity_s = route.get('last_activity', None)
            container_id = route.get('container_id', None)
            if container_id:
                result = (path, container_id)
                if container_id in living_set:
                    try:
                        last_activity = datetime.strptime(last_activity_s, _date_fmt)
                        started = self.started.get(container_id, None)
                        self.routes.add(result)
                        if started and last_activity < idle_cutoff:
                            app_log.info("Culling %s, idle since %s", path, last_activity)
                            self.stale_routes.append(result)
                        elif started and started < started_cutoff:
                            app_log.info("Culling %s, up since %s", path, started)
                            self.stale_routes.append(result)
                        else:
                            app_log.debug("Container %s up since %s, idle since %s",
                                          path, started, last_activity)
                            self.live_routes.append(result)
                    except ValueError as e:
                        app_log.warning("Ignoring a proxy route with an unparsable activity date: %s", e)
                else:
                    # The container doesn't correspond to a living container.
                    self.zombie_routes.append(result)

    @gen.coroutine
    def _proxy_routes(self):
        routes = []

        url = "{}/api/routes".format(self.proxy_endpoint)
        headers = {"Authorization": "token {}".format(self.proxy_token)}
        req = HTTPRequest(url, method="GET", headers=headers)
        http_client = AsyncHTTPClient()
        try:
            resp = yield http_client.fetch(req)
            results = json.loads(resp.body.decode('utf8', 'replace'))
            raise gen.Return(results)
        except HTTPError as e:
            app_log.error("Unable to list existing proxy entries: %s", e)
            raise gen.Return({})
