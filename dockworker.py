import datetime
import json
import os

from concurrent.futures import ThreadPoolExecutor

import docker

import tornado
import tornado.options

from tornado.log import app_log

from tornado import gen, web
from tornado import ioloop
from tornado.httputil import url_concat
from tornado.httpclient import HTTPRequest, HTTPError, AsyncHTTPClient

class AsyncDockerClient():
    '''Completely ridiculous wrapper for a Docker client that returns futures
    on every single docker method called on it, configured with an executor.
    If no executor is passed, it defaults ThreadPoolExecutor(max_workers=2).
    '''
    def __init__(self, docker_client, executor=None):
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=2)
        self._docker_client = docker_client
        self.executor = executor

    def __getattr__(self, name):
        '''Creates a function, based on docker_client.name that returns a
        Future. If name is not a callable, returns the attribute directly.
        '''
        fn = getattr(self._docker_client, name)

        # Make sure it really is a function first
        if not callable(fn):
            return fn

        def method(*args, **kwargs):
            return self.executor.submit(fn, *args, **kwargs)

        return method

@gen.coroutine
def cull_idle(docker_client, proxy_api_endpoint, proxy_token, delta=None):
    if delta is None:
        delta = datetime.timedelta(minutes=60)
    http_client = AsyncHTTPClient()

    dt = datetime.datetime.utcnow() - delta
    timestamp = dt.isoformat() + 'Z'

    routes_url = proxy_api_endpoint + "/api/routes"
    app_log.info("Culling on {}".format(routes_url))

    url = url_concat(routes_url,
                     {'inactive_since': timestamp})

    headers = {"Authorization": "token {}".format(proxy_token)}

    app_log.debug("Fetching %s", url)
    req = HTTPRequest(url,
                      method="GET",
                      headers=headers)

    reply = yield http_client.fetch(req)
    data = json.loads(reply.body.decode('utf8', 'replace'))

    if not data:
        app_log.debug("No stale routes to cull")

    for base_path, route in data.items():
        container_id = route.get('container_id', None)
        if container_id:
            app_log.info("shutting down container %s at %s", container_id, base_path)
            try:
                yield docker_client.kill(container_id)
                yield docker_client.remove_container(container_id)
            except Exception as e:
                app_log.error("Unable to cull {}: {}".format(container_id, e))
        else:
            app_log.error("No container found for %s", base_path)

        app_log.info("removing %s from proxy", base_path)
        req = HTTPRequest(routes_url + base_path,
                          method="DELETE",
                          headers=headers)
        try:
            reply = yield http_client.fetch(req)
        except HTTPError as e:
            app_log.error("Failed to delete route %s: %s", base_path, e)


if __name__ == "__main__":
    # For now, we'll assume this is the culling client
    tornado.options.define('cull_timeout', default=3600,
        help="Timeout (s) for culling idle"
    )
    tornado.options.define('port', default=9999,
        help="port for the dock worker to listen on"
    )
    tornado.options.define('max_dock_workers', default=24,
        help="Maximum number of docker workers"
    )

    tornado.options.parse_command_line()
    opts = tornado.options.options

    proxy_token = os.environ['CONFIGPROXY_AUTH_TOKEN']
    proxy_api_endpoint = os.environ.get('CONFIGPROXY_ENDPOINT', "http://127.0.0.1:8001")
    docker_host = os.environ.get('DOCKER_HOST', 'unix://var/run/docker.sock')

    blocking_docker_client = docker.Client(base_url=docker_host, timeout=10)

    executor = ThreadPoolExecutor(max_workers=opts.max_dock_workers)
    
    AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")
    async_docker_client = AsyncDockerClient(blocking_docker_client,
                                            executor)

    # check for idle containers and cull them
    cull_timeout = opts.cull_timeout

    if cull_timeout:
        delta = datetime.timedelta(seconds=cull_timeout)
        cull_ms = cull_timeout * 1e3
        app_log.info("Culling every %i seconds", cull_timeout)
        culler = tornado.ioloop.PeriodicCallback(
            lambda : cull_idle(async_docker_client, proxy_api_endpoint, proxy_token, delta),
            cull_ms
        )
        culler.start()
    else:
        app_log.info("Not culling idle containers")

    ioloop.IOLoop.current().start()
