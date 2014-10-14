from concurrent.futures import ThreadPoolExecutor
from collections import deque, namedtuple
from datetime import datetime, timedelta
from tornado import gen
from tornado import ioloop
from tornado.log import app_log
from tornado.httpclient import HTTPRequest, HTTPError, AsyncHTTPClient

import string
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


class SpawnPool():
    '''Manage a pool of precreated Docker containers.'''

    def __init__(self,
                 proxy_endpoint,
                 proxy_token,
                 docker_host='unix://var/run/docker.sock',
                 version='1.12',
                 timeout=20,
                 max_workers=64):
        '''Create a new, empty spawn pool, with nothing preallocated.'''

        self.proxy_endpoint = proxy_endpoint
        self.proxy_token = proxy_token

        self.docker = dockworker.DockerSpawner(docker_host, version, timeout, max_workers)

        self.available = deque()
        self.taken = set()

    def prepare(self, count):
        '''Synchronously pre-allocate a set number of containers, ready to serve.'''

        app_log.info("Preparing %i containers.", count)
        for i in xrange(0, count):
            nb_container = ioloop.IOLoop.instance().run_sync(self._launch_notebook_server)
            self.available.append(nb_container)
            app_log.debug("Pre-launched container [%s].", nb_container)
        app_log.info("%i containers successfully prepared.", count)

    def acquire(self):
        '''Acquire a preallocated container and returns its user path.'''

        next = self.available.pop()
        self.taken.add(next)
        return next

    @gen.coroutine
    def release(self, container):
        '''Release a container previously returned by acquire. Destroy the container and create a
        new one to take its place.'''

        try:
            app_log.info("Killing used container [%s].", container)
            yield self.docker.kill(container.id)

            app_log.debug("Removing killed container [%s].", container)
            yield self.docker.remove_container(container.id)
        except Exception as e:
            app_log.error("Unable to cull container [%s]: %s", container, e)

        app_log.debug("Removing container [%s] from the proxy.", container)
        http_client = AsyncHTTPClient()
        proxy_url = "{}/api/routes/{}".format(self.proxy_endpoint, path)
        headers = {"Authorization": "token {}".format(self.proxy_token)}
        req = HTTPRequest(proxy_url, method="DELETE", headers=headers)
        try:
            yield http_client.fetch(req)
        except HTTPError as e:
            app_log.error("Failed to delete route [%s]: %s", container, e)

        self.taken.discard(container)
        app_log.debug("Launching a replacement container.")
        new_container = yield self._launch_notebook_server()
        self.available.append(new_container)
        app_log.info("Replacement container [%s] is up and ready to go.", new_container)

    @gen.coroutine
    def cull(self, delta=None):
        if delta is None:
            delta = timedelta(minutes=60)
        http_client = AsyncHTTPClient()
        app_log.debug("The culling has begun.")
        reaped = 0

        dt = datetime.utcnow() - delta
        cutoff = dt.isoformat() + 'Z'
        url = url_concat("{}/api/routes".format(self.proxy_endpoint), {'inactive_since': cutoff})
        headers = {"Authorization": "token {}".format(self.proxy_token)}

        app_log.debug("Fetching sessions inactive since [%s].", cutoff)
        req = HTTPRequest(url, method="GET", headers=headers)

        try:
            resp = yield http_client.fetch(req)
            results = json.loads(reply.body.decode('utf8', 'replace'))

            if not results:
                app_log.debug("No stale routes to cull.")

            for base_path, route in results.items():
                container_id = route.get('container_id', None)
                if container_id:
                    container = PooledContainer(id=container_id, path=base_path)
                    yield self.release(container)
                    count += 1
        except HTTPError as e:
            app_log.error("Failed to list stale routes: %s", e)

        app_log.debug("The culling has reaped %i souls (containers).", reaped)

    @gen.coroutine
    def _launch_notebook_server(self):
        '''Launch a new notebook server in a fresh container and register it with the proxy.'''

        path = user_prefix()

        app_log.debug("Launching new notebook server for user [%s].", path)
        container_id, host_ip, host_port = yield self.docker.create_notebook_server(base_path=path)
        app_log.debug("Created notebook server for [%s] at [%s:%s]", path, host_ip, host_port)

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

        raise gen.Return(PooledContainer(id=container_id, path=path))
