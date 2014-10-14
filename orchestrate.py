#!/usr/bin/env python
# -*- coding: utf-8 -*-

import errno
import datetime
import json
import os
import random
import socket
import string
import time
import uuid

from concurrent.futures import ThreadPoolExecutor

import docker

import tornado
import tornado.options
from tornado.log import app_log
from tornado.web import RequestHandler

from tornado import gen, web
from tornado import ioloop

from tornado.httputil import url_concat
from tornado.httpclient import HTTPRequest, HTTPError, AsyncHTTPClient

import dockworker
from dockworker import AsyncDockerClient
from spawnpool import SpawnPool

AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")

def sample_with_replacement(a, size=12):
    '''Get a random path. If Python had sampling with replacement built in,
    I would use that. The other alternative is numpy.random.choice, but
    numpy is overkill for this tiny bit of random pathing.'''
    return "".join([random.choice(a) for x in range(size)])

class LoadingHandler(RequestHandler):
    def get(self, path=None):
        self.render("loading.html", path=path)

class SpawnHandler(RequestHandler):

    @gen.coroutine
    def get(self, path=None):
        '''Spawns a brand new server'''

        # Take out a leading slash
        redirect_uri = self.redirect_uri.lstrip("/")

        if path is None:
            # No path. Assign a prelaunched container from the pool and redirect to it.
            prefix = self.pool.acquire().path

            url = "/{}/{}".format(prefix, redirect_uri)
            app_log.debug("Redirecting [%s] -> [%s].", self.request.path, url)
            self.redirect(url, permanent=False)
        else:
            prefix = path.lstrip('/').split('/', 1)[0]
            app_log.info("Provisioning a new ad-hoc container for [%s].", prefix)
            self.write("Provisioning a new ad-hoc container for [{}].".format(prefix))

            container_id, ip, port = yield self.spawner.create_notebook_server(prefix,
                    image=self.image, ipython_executable=self.ipython_executable,
                    mem_limit=self.mem_limit, cpu_shares=self.cpu_shares,
                    container_ip=self.container_ip,
                    container_port=self.container_port)

            app_log.debug("Launched container [%s] at [%s:%s] with ID [%s]",
                          prefix,
                          ip,
                          port,
                          container_id)
            yield self.proxy(ip, port, prefix, container_id)

            # Wait for the notebook server to come up.
            yield self.wait_for_server(ip, port, prefix)

            if path is None:
                url = "/{}/{}".format(prefix, redirect_uri)
            else:
                url = path
                if not url.startswith('/'):
                    url = '/' + url
            app_log.debug("Redirecting [%s] -> [%s].", self.request.path, url)
            self.redirect(url, permanent=False)

    @gen.coroutine
    def wait_for_server(self, ip, port, path, timeout=10, wait_time=0.2):
        '''Wait for a server to show up at ip:port'''
        app_log.info("Waiting for {}:{}".format(ip,port))
        loop = ioloop.IOLoop.current()
        tic = loop.time()
        while loop.time() - tic < timeout:
            try:
                socket.create_connection((ip, port))
            except socket.error as e:
                app_log.warn("Socket error on boot {}".format(e))
                if e.errno != errno.ECONNREFUSED:
                    app_log.warn("Error attempting to connect to %s:%i - %s",
                        ip, port, e,
                    )
                yield gen.Task(loop.add_timeout, loop.time() + wait_time)
            else:
                break

        # Fudge factor of IPython notebook bootup
        # TODO: Implement a webhook in IPython proper to call out when the
        # notebook server is booted
        yield gen.Task(loop.add_timeout, loop.time() + .5)

        # Now make sure that we can reach the Notebook server
        http_client = AsyncHTTPClient()

        req = HTTPRequest("http://{}:{}/{}".format(ip,port,path))

        while loop.time() - tic < timeout:
            try:
                resp = yield http_client.fetch(req)
            except HTTPError as http_error:
                code = http_error.code
                app_log.info("Booting /{}, getting {}".format(path,code))
                yield gen.Task(loop.add_timeout, loop.time() + wait_time)
            else:
                break

    @gen.coroutine
    def proxy(self, ip, port, base_path, container_id):
        http_client = AsyncHTTPClient()
        headers = {"Authorization": "token {}".format(self.proxy_token)}

        proxy_endpoint = self.proxy_endpoint + "/api/routes/{}".format(base_path)
        body = json.dumps({
            "target": "http://{}:{}".format(ip, port),
            "container_id": container_id,
        })

        app_log.info("proxying %s to %s", base_path, port)

        req = HTTPRequest(proxy_endpoint,
                          method="POST",
                          headers=headers,
                          body=body)

        resp = yield http_client.fetch(req)

    @property
    def spawner(self):
        return self.settings['spawner']

    @property
    def pool(self):
        return self.settings['pool']

    @property
    def proxy_token(self):
        return self.settings['proxy_token']

    @property
    def proxy_endpoint(self):
        return self.settings['proxy_endpoint']

    @property
    def container_ip(self):
        return self.settings['container_ip']

    @property
    def container_port(self):
        return self.settings['container_port']

    @property
    def mem_limit(self):
        return self.settings['mem_limit']

    @property
    def cpu_shares(self):
        return self.settings['cpu_shares']

    @property
    def image(self):
        return self.settings['image']

    @property
    def ipython_executable(self):
        return self.settings['ipython_executable']

    @property
    def redirect_uri(self):
        return self.settings['redirect_uri']

def main():
    tornado.options.define('cull_timeout', default=3600,
        help="Timeout (s) for culling idle"
    )
    tornado.options.define('container_ip', default='127.0.0.1',
        help="IP address for containers to bind to"
    )
    tornado.options.define('container_port', default='8888',
        help="Port for containers to bind to"
    )
    tornado.options.define('ipython_executable', default='ipython3',
        help="IPython Notebook startup (e.g. ipython, ipython2, ipython3)"
    )
    tornado.options.define('port', default=9999,
        help="port for the main server to listen on"
    )
    tornado.options.define('max_dock_workers', default=24,
        help="Maximum number of docker workers"
    )
    tornado.options.define('mem_limit', default="512m",
        help="Limit on Memory, per container"
    )
    tornado.options.define('cpu_shares', default=None,
        help="Limit CPU shares, per container"
    )
    tornado.options.define('image', default="jupyter/demo",
        help="Docker container to spawn for new users. Must be on the system already"
    )
    tornado.options.define('docker_version', default="1.13",
        help="Version of the Docker API to use"
    )
    tornado.options.define('redirect_uri', default="/tree",
        help="URI to redirect users to upon initial notebook launch"
    )

    tornado.options.parse_command_line()
    opts = tornado.options.options

    handlers = [
        (r"/", LoadingHandler),
        (r"/spawn/?(/user-\w+/.+)?", SpawnHandler),
        (r"/(user-\w+)/.*", LoadingHandler),
    ]

    proxy_token = os.environ['CONFIGPROXY_AUTH_TOKEN']
    proxy_endpoint = os.environ.get('CONFIGPROXY_ENDPOINT', "http://127.0.0.1:8001")
    docker_host = os.environ.get('DOCKER_HOST', 'unix://var/run/docker.sock')

    blocking_docker_client = docker.Client(base_url=docker_host, version=opts.docker_version, timeout=20)

    executor = ThreadPoolExecutor(max_workers=opts.max_dock_workers)

    async_docker_client = AsyncDockerClient(blocking_docker_client,
                                            executor)

    spawner = dockworker.DockerSpawner(docker_host,
                                       version=opts.docker_version,
                                       timeout=20,
                                       max_workers=opts.max_dock_workers)

    pool = SpawnPool(proxy_endpoint=proxy_endpoint,
                     proxy_token=proxy_token,
                     docker_host=docker_host,
                     version=opts.docker_version,
                     timeout=20,
                     max_workers=opts.max_dock_workers)

    settings = dict(
        static_path=os.path.join(os.path.dirname(__file__), "static"),
        cookie_secret=uuid.uuid4(),
        xsrf_cookies=True,
        debug=True,
        spawner=spawner,
        pool=pool,
        autoescape=None,
        container_ip = opts.container_ip,
        container_port = opts.container_port,
        ipython_executable = opts.ipython_executable,
        proxy_token=proxy_token,
        template_path=os.path.join(os.path.dirname(__file__), 'templates'),
        proxy_endpoint=proxy_endpoint,
        mem_limit=opts.mem_limit,
        cpu_shares=opts.cpu_shares,
        image=opts.image,
        redirect_uri=opts.redirect_uri,
    )

    # Pre-launch a set number of containers, ready to serve.
    pool.prepare(3)

    # check for idle containers and cull them
    cull_timeout = opts.cull_timeout

    if cull_timeout:
        delta = datetime.timedelta(seconds=cull_timeout)
        cull_ms = cull_timeout * 1e3
        app_log.info("Culling every %i seconds", cull_timeout)
        culler = tornado.ioloop.PeriodicCallback(
            lambda : pool.cull(),
            cull_ms
        )
        culler.start()
    else:
        app_log.info("Not culling idle containers")

    app_log.info("Listening on {}".format(opts.port))

    application = tornado.web.Application(handlers, **settings)
    application.listen(opts.port)
    tornado.ioloop.IOLoop().instance().start()

if __name__ == "__main__":
    main()
