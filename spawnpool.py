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


class EmptyPoolError():
    '''Exception raised when a container is requested from an empty pool.'''

    pass

class SpawnPool():
    '''Manage a pool of precreated Docker containers.'''

    def __init__(self,
                 proxy_endpoint,
                 proxy_token,
                 spawner,
                 container_config,
                 capacity):
        '''Create a new, empty spawn pool, with nothing preallocated.'''

        self.docker = spawner
        self.container_config = container_config
        self.capacity = capacity

        self.proxy_endpoint = proxy_endpoint
        self.proxy_token = proxy_token

        self.available = deque()
        self.releasing = set()

    @gen.coroutine
    def prelaunch(self):
        '''Pre-allocate a set number of containers, ready to serve.'''

        existing = yield self._clean_orphaned_containers()

        count = max(self.capacity - existing, 0)
        app_log.info("Preparing %i new containers.", count)
        yield [self._launch_container() for i in xrange(0, count)]
        app_log.info("%i containers successfully prepared.", count)

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

        yield self.release(self.acquire(), False)
        launched = yield self._launch_container(path)
        raise gen.Return(launched)

    @gen.coroutine
    def release(self, container, replace_if_room=True):
        '''Shut down a container and delete its proxy entry.

        Destroy the container in an orderly fashion. If requested and capacity is remaining, create
        a new one to take its place.'''

        if container.id in self.releasing:
            # This can happen if culling runs again before the previous run finishes deleting
            # everything.
            app_log.debug("Container [%s] is already being released.", container)
            return

        self.releasing.add(container.id)

        try:
            app_log.info("Shutting down used container [%s].", container)
            yield self.docker.shutdown_notebook_server(container.id)
            app_log.debug("Inactive container [%s] has been shut down.", container)
        except Exception as e:
            app_log.error("Unable to cull container [%s]: %s", container, e)

        app_log.debug("Removing container [%s] from the proxy.", container)
        http_client = AsyncHTTPClient()
        proxy_url = "{}/api/routes/{}".format(self.proxy_endpoint, container.path.lstrip('/'))
        headers = {"Authorization": "token {}".format(self.proxy_token)}
        req = HTTPRequest(proxy_url, method="DELETE", headers=headers)
        try:
            yield http_client.fetch(req)
        except HTTPError as e:
            app_log.error("Failed to delete route [%s]: %s", proxy_url, e)

        if replace_if_room:
            running = yield self.docker.list_notebook_servers(self.container_config,
                                                                    all=False)
            if len(running) + 1 <= self.capacity:
                app_log.debug("Launching a replacement container.")
                yield self._launch_container()
            else:
                app_log.info("Declining to launch a new container because [%i] containers are" +
                             " already running, and the capacity is [%i].",
                             len(running), self.capacity)

        self.releasing.remove(container.id)

    @gen.coroutine
    def cull(self, max_age=None):
        '''Destroy and replace any used containers that have been idle.

        A container is considered "used" if it isn't still present in the pool. If no max_age is
        specified, an hour is used.'''

        if max_age is None:
            max_age = timedelta(minutes=60)
        http_client = AsyncHTTPClient()
        app_log.debug("The culling has begun.")

        dt = datetime.utcnow() - max_age
        cutoff = dt.isoformat() + 'Z'
        url = url_concat("{}/api/routes".format(self.proxy_endpoint), {'inactive_since': cutoff})
        headers = {"Authorization": "token {}".format(self.proxy_token)}

        app_log.debug("Fetching sessions inactive since [%s].", cutoff)
        req = HTTPRequest(url, method="GET", headers=headers)

        reap = []

        try:
            resp = yield http_client.fetch(req)
            results = json.loads(resp.body.decode('utf8', 'replace'))

            if not results:
                app_log.debug("No stale routes to cull.")

            pooled_ids = self._pooled_ids()

            for base_path, route in results.items():
                container_id = route.get('container_id', None)
                if container_id:
                    # Don't cull containers that are waiting in the pool and haven't been used
                    # yet.
                    if container_id in pooled_ids:
                        app_log.debug("Not culling unused container [%s].", container_id)
                    else:
                        reap.append(PooledContainer(id=container_id, path=base_path))
        except HTTPError as e:
            app_log.error("Failed to list stale routes: %s", e)

        if reap:
            yield [self.release(each) for each in reap]
        else:
            app_log.debug("No stale containers to reap.")

        app_log.debug("The culling has reaped %i souls (containers).", len(reap))

    @gen.coroutine
    def _launch_container(self, path=None):
        '''Launch a new notebook server in a fresh container, register it with the proxy, and
        add it to the pool.'''

        if path is None:
            path = user_prefix()

        app_log.debug("Launching new notebook server for user [%s].", path)
        container_id, host_ip, host_port = yield self.docker.create_notebook_server(base_path=path,
                                                                                    config=self.container_config)
        app_log.debug("Created notebook server for [%s] at [%s:%s]", path, host_ip, host_port)

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

        app_log.debug("Proxying notebook [%s] to port [%s].", path, host_port)
        req = HTTPRequest(proxy_endpoint,
                          method="POST",
                          headers=headers,
                          body=body)
        try:
            yield http_client.fetch(req)
            app_log.info("Proxied notebook [%s] to port [%s].", path, host_port)
        except HTTPError as e:
            app_log.error("Failed to create proxy route to [%s]: %s", path, e)

        container = PooledContainer(id=container_id, path=path)
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

    @gen.coroutine
    def _clean_orphaned_containers(self):
        '''Clean up any pre-existing, stale containers.

        These are containers that exist in Docker, but have no corresponding proxy entry. The
        regular culling process will handle removing active containers as they expire and
        re-launching new ones up to the pool capacity.

        Returns the number of existing containers that were *not* cleaned out, so that the
        prelaunch process can take this into account and not overload the server.'''

        app_log.info("Cleaning out any orphaned containers.")

        def is_alive(container):
            return container['Status'].startswith('Up')

        docker_results = yield self.docker.list_notebook_servers(self.container_config,
                                                                 all=True)

        # Remove any stopped containers.
        stopped_ids = [container['Id'] for container in docker_results if not is_alive(container)]
        app_log.debug("Containers that are stopped: [%i]", len(stopped_ids))
        if stopped_ids:
            yield [self.docker.shutdown_notebook_server(id, alive=False) for id in stopped_ids]
            app_log.info("Removed [%i] stopped containers.", len(stopped_ids))

        # Identify the living containers.
        docker_ids = [container['Id'] for container in docker_results if is_alive(container)]
        app_log.debug("Containers that already exist in Docker: [%i]", len(docker_ids))

        # Identity the containers that have live entries in the proxy.
        proxy_ids = set()
        url = "{}/api/routes".format(self.proxy_endpoint)
        headers = {"Authorization": "token {}".format(self.proxy_token)}
        req = HTTPRequest(url, method="GET", headers=headers)
        http_client = AsyncHTTPClient()
        try:
            resp = yield http_client.fetch(req)
            results = json.loads(resp.body.decode('utf8', 'replace'))

            for base_path, route in results.items():
                container_id = route.get('container_id', None)
                if container_id:
                    proxy_ids.add(container_id)
        except HTTPError as e:
            app_log.error("Unable to list existing proxy entries: %s", e)
            return
        app_log.debug("Containers that are routed in the proxy: [%i]", len(proxy_ids))

        # Shut down any containers that are alive, but don't have proxy entries.
        stale_ids = [id for id in docker_ids if id not in proxy_ids]
        app_log.debug("Deleting [%i] stale containers from Docker.", len(stale_ids))
        yield [self.docker.shutdown_notebook_server(id) for id in stale_ids]

        # Return the number of containers that are still running.
        left = len(docker_ids) - len(stale_ids)
        app_log.debug("There are [%i] active containers already running on this server.", left)
        raise gen.Return(left)

    def _pooled_ids(self):
        '''Build a set of container IDs that are currently waiting in the pool.'''

        return set(container.id for container in self.available)
