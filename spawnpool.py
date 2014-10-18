from concurrent.futures import ThreadPoolExecutor
from collections import deque, namedtuple
from datetime import datetime, timedelta
from tornado import gen
from tornado import ioloop
from tornado.log import app_log
from tornado.httpclient import HTTPRequest, HTTPError, AsyncHTTPClient
from tornado.httputil import url_concat

import errno
import string
import socket
import pytz
import random
import json
import dockworker

AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")


def sample_with_replacement(a, size=12):
    '''Get a random path. If Python had sampling with replacement built in,
    I would use that. The other alternative is numpy.random.choice, but
    numpy is overkill for this tiny bit of random pathing.'''
    return "".join([random.choice(a) for x in range(size)])


def user_prefix():
    '''Generate a fresh user- path for a new container.'''
    return "user-" + sample_with_replacement(string.ascii_letters + string.digits)


PooledContainer = namedtuple('PooledContainer', ['id', 'path'])


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
                 max_age):
        '''Create a new, empty spawn pool, with nothing preallocated.'''

        self.spawner = spawner
        self.container_config = container_config
        self.capacity = capacity
        self.max_age = max_age

        self.proxy_endpoint = proxy_endpoint
        self.proxy_token = proxy_token

        self.available = deque()

    def acquire(self):
        '''Acquire a preallocated container and returns its user path.

        An EmptyPoolError is raised if no containers are ready.'''

        if not self.available:
            raise EmptyPoolError()

        return self.available.pop()

    @gen.coroutine
    def adhoc(self, path):
        '''Launch a container with a fixed path by taking the place of an existing container from
        the pool.'''

        to_release = self.acquire()
        app_log.debug("Discarding container [%s] to create an ad-hoc replacement.", to_release)
        yield self.release(to_release, False)

        launched = yield self._launch_container(path=path, enpool=False)
        raise gen.Return(launched)

    @gen.coroutine
    def release(self, container, replace_if_room=True):
        '''Shut down a container and delete its proxy entry.

        Destroy the container in an orderly fashion. If requested and capacity is remaining, create
        a new one to take its place.'''

        try:
            app_log.info("Releasing container [%s].", container)
            yield [
                self.spawner.shutdown_notebook_server(container.id),
                self._proxy_remove(container.path)
            ]
            app_log.debug("Container [%s] has been released.", container)
        except Exception as e:
            app_log.error("Unable to release container [%s]: %s", container, e)
            return

        if replace_if_room:
            running = yield self.spawner.list_notebook_servers(self.container_config,
                                                                    all=False)
            if len(running) + 1 <= self.capacity:
                app_log.debug("Launching a replacement container.")
                yield self._launch_container()
            else:
                app_log.info("Declining to launch a new container because [%i] containers are" +
                             " already running, and the capacity is [%i].",
                             len(running), self.capacity)

    @gen.coroutine
    def heartbeat(self):
        '''Examine the pool for any missing, stopped, or idle containers, and replace them.

        A container is considered "used" if it isn't still present in the pool. If no max_age is
        specified, an hour is used.'''

        app_log.debug("Heartbeat begun. Measuring current state.")

        diagnosis = Diagnosis(self.max_age,
                              self.spawner,
                              self.container_config,
                              self.proxy_endpoint,
                              self.proxy_token)
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
            container = PooledContainer(path=path, id=id)
            tasks.append(self.release(container, replace_if_room=True))

        # Normalize the container count to its initial capacity by scheduling deletions if we're
        # over or scheduling launches if we're under.
        current = len(diagnosis.living_container_ids)
        under = xrange(current, self.capacity)
        over = xrange(self.capacity, current)

        if under:
            app_log.debug("Launching [%i] new containers to populate the pool.", len(under))
        for i in under:
            tasks.append(self._launch_container())

        if over:
            app_log.debug("Removing [%i] containers to diminish the pool.", len(over))
        for i in over:
            try:
                pooled = self.acquire()
                app_log.debug("Releasing container [%s] to shrink the pool.", pooled.id)
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

    @gen.coroutine
    def _launch_container(self, path=None, enpool=True):
        '''Launch a new notebook server in a fresh container, register it with the proxy, and
        add it to the pool.'''

        if path is None:
            path = user_prefix()

        app_log.debug("Launching new notebook server at path [%s].", path)
        create_result = yield self.spawner.create_notebook_server(base_path=path,
                                                                  container_config=self.container_config)
        container_id, host_ip, host_port = create_result
        app_log.debug("Created notebook server for path [%s] at [%s:%s]", path, host_ip, host_port)

        # Wait for the server to launch within the container before adding it to the pool or
        # serving it to a user.
        yield self._wait_for_server(host_ip, host_port, path)

        http_client = AsyncHTTPClient()
        headers = {"Authorization": "token {}".format(self.proxy_token)}

        proxy_endpoint = "{}/api/routes/{}".format(self.proxy_endpoint, path)
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

        container = PooledContainer(id=container_id, path=path)
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
        req = HTTPRequest("http://{}:{}/{}".format(ip, port, path))

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


class Diagnosis():
    '''Collect and organize information to self-heal a SpawnPool.

    Measure the current state of Docker and the proxy routes and scan for anomalies so the pool can
    correct them. This includes zombie containers, containers that are running but not routed in the
    proxy, proxy routes that exist without a corresponding container, or other strange conditions.'''

    def __init__(self, cull_time, spawner, container_config, proxy_endpoint, proxy_token):
        self.spawner = spawner
        self.container_config = container_config
        self.proxy_endpoint = proxy_endpoint
        self.proxy_token = proxy_token
        self.cull_time = cull_time

    @gen.coroutine
    def observe(self):
        '''Collect Ground Truth of what's actually running from Docker and the proxy.'''

        results = yield {
            "docker": self.spawner.list_notebook_servers(self.container_config, all=True),
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

        cutoff = datetime.utcnow() - self.cull_time

        # Sort proxy routes into living, stale, and zombie routes.
        living_set = set(self.living_container_ids)
        for path, route in results["proxy"].items():
            last_activity_s = route.get('last_activity', None)
            container_id = route.get('container_id', None)
            if container_id:
                result = (path, container_id)
                if container_id in living_set:
                    try:
                        last_activity = datetime.strptime(last_activity_s, '%Y-%m-%dT%H:%M:%S.%fZ')

                        self.routes.add(result)
                        if last_activity >= cutoff:
                            self.live_routes.append(result)
                        else:
                            self.stale_routes.append(result)
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
