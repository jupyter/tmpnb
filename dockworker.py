import datetime
import json

from concurrent.futures import ThreadPoolExecutor
from collections import namedtuple

import docker

from tornado.log import app_log

from tornado import gen, web
from tornado.httputil import url_concat
from tornado.httpclient import HTTPRequest, HTTPError, AsyncHTTPClient

AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")

ContainerConfig = namedtuple('ImageConfig', [
    'image', 'ipython_executable', 'mem_limit', 'cpu_shares', 'container_ip', 'container_port'
])


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


class DockerSpawner():
    def __init__(self,
                 docker_host='unix://var/run/docker.sock',
                 version='1.12',
                 timeout=20,
                 max_workers=64):

        blocking_docker_client = docker.Client(base_url=docker_host,
                                               version=version,
                                               timeout=timeout)

        executor = ThreadPoolExecutor(max_workers=max_workers)

        async_docker_client = AsyncDockerClient(blocking_docker_client,
                                                executor)
        self.docker_client = async_docker_client

    @gen.coroutine
    def create_notebook_server(self, base_path, config):
        '''Creates a notebook_server running off of `base_path`.

        Returns the container_id, ip, port in a Future.'''

        templates = ['/srv/ga',
                     '/srv/ipython/IPython/html',
                     '/srv/ipython/IPython/html/templates']

        tornado_settings = {'template_path': templates}

        ipython_args = [
                "notebook", "--no-browser",
                "--port {}".format(config.container_port),
                "--ip=0.0.0.0",
                "--NotebookApp.base_url=/{}".format(base_path),
                "--NotebookApp.tornado_settings=\"{}\"".format(tornado_settings)
        ]

        ipython_command = config.ipython_executable + " " + " ".join(ipython_args)

        command = [
            "/bin/sh",
            "-c",
            ipython_command
        ]

        resp = yield self.docker_client.create_container(image=config.image,
                                                         command=command,
                                                         mem_limit=config.mem_limit,
                                                         cpu_shares=config.cpu_shares)

        docker_warnings = resp['Warnings']
        if docker_warnings is not None:
            app_log.warn(docker_warnings)

        container_id = resp['Id']
        app_log.info("Created container {}".format(container_id))

        yield self.docker_client.start(container_id,
                                       port_bindings={config.container_port: (config.container_ip,)})

        container_network = yield self.docker_client.port(container_id,
                                                          config.container_port)

        host_port = container_network[0]['HostPort']
        host_ip = container_network[0]['HostIp']

        raise gen.Return((container_id, host_ip, int(host_port)))

@gen.coroutine
def cull_idle(docker_client, proxy_endpoint, proxy_token, delta=None):
    if delta is None:
        delta = datetime.timedelta(minutes=60)
    http_client = AsyncHTTPClient()

    dt = datetime.datetime.utcnow() - delta
    timestamp = dt.isoformat() + 'Z'

    routes_url = proxy_endpoint + "/api/routes"

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
                yield docker_client.stop(container_id)
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
