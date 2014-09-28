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

from dockworker import cull_idle

AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")

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
        if path is None:
            # no path, use random prefix
            prefix = "user-" + sample_with_replacement(string.ascii_letters +
                                                       string.digits)
        else:
            prefix = path.lstrip('/').split('/', 1)[0]

        self.write("Initializing {}".format(prefix))

        container_id, port = yield self.create_notebook_server(prefix)

        yield self.proxy(port, prefix, container_id)

        # Wait for the notebook server to come up.
        yield self.wait_for_server("127.0.0.1", port, prefix)

        if path is None:
            url = "/%s/notebooks/Welcome.ipynb" % prefix
        else:
            url = path
            if not url.startswith('/'):
                url = '/' + url
        app_log.debug("redirecting %s -> %s", self.request.path, url)
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

    @property
    def docker_client(self):
        return self.settings['docker_client']

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
    def mem_limit(self):
        return self.settings['mem_limit']

    @gen.coroutine
    def create_notebook_server(self, base_path):
        '''
        POST /containers/create
        '''
        # TODO: Use tornado AsyncHTTPClient instead of docker-py

        docker_client = self.docker_client

        env = {"RAND_BASE": base_path}
        resp = yield docker_client.create_container(image="jupyter/tmpnb",
                                                    environment=env,
                                                    mem_limit=self.mem_limit)
        container_id = resp['Id']
        app_log.info("Created container {}".format(container_id))

        yield docker_client.start(container_id, port_bindings={8888: (self.container_ip,)})
        ports = yield docker_client.port(container_id, 8888)
        port = ports[0]['HostPort']

        raise gen.Return((container_id, int(port)))

    @gen.coroutine
    def proxy(self, port, base_path, container_id):
        http_client = AsyncHTTPClient()
        headers = {"Authorization": "token {}".format(self.proxy_token)}

        proxy_endpoint = self.proxy_endpoint + "/api/routes/{}".format(base_path)
        body = json.dumps({
            "target": "http://localhost:{}".format(port),
            "container_id": container_id,
        })

        app_log.info("proxying %s to %s", base_path, port)

        req = HTTPRequest(proxy_endpoint,
                          method="POST",
                          headers=headers,
                          body=body)

        resp = yield http_client.fetch(req)

def main():
    tornado.options.define('cull_timeout', default=3600,
        help="Timeout (s) for culling idle"
    )
    tornado.options.define('container_ip', default='127.0.0.1',
        help="IP address for containers to bind to"
    )
    tornado.options.define('port', default=9999,
        help="port for the main server to listen on"
    )
    tornado.options.define('max_dock_workers', default=24,
        help="Maximum number of docker workers"
    )
    tornado.options.define('mem_limit', default='512m',
        help="Limit on Memory, per container"
    )

    tornado.options.parse_command_line()
    opts = tornado.options.options

    handlers = [
        (r"/", LoadingHandler),
        (r"/spawn/?(/.+)?", SpawnHandler),
        (r"/(user-\w+)/.*", LoadingHandler),
    ]

    proxy_token = os.environ['CONFIGPROXY_AUTH_TOKEN']
    proxy_endpoint = os.environ.get('CONFIGPROXY_ENDPOINT', "http://localhost:8001")
    docker_host = os.environ.get('DOCKER_HOST', 'unix://var/run/docker.sock')
    
    blocking_docker_client = docker.Client(base_url=docker_host, timeout=10)

    executor = ThreadPoolExecutor(max_workers=opts.max_dock_workers)
    
    async_docker_client = AsyncDockerClient(blocking_docker_client,
                                            executor)

    settings = dict(
        static_path=os.path.join(os.path.dirname(__file__), "static"),
        cookie_secret=uuid.uuid4(),
        xsrf_cookies=True,
        debug=True,
        autoescape=None,
        docker_client=async_docker_client,
        container_ip = opts.container_ip,
        proxy_token=proxy_token,
        template_path=os.path.join(os.path.dirname(__file__), 'templates'),
        proxy_endpoint=proxy_endpoint,
        mem_limit=opts.mem_limit,
    )
    
    # check for idle containers and cull them
    cull_timeout = opts.cull_timeout
    
    if cull_timeout:
        delta = datetime.timedelta(seconds=cull_timeout)
        cull_ms = cull_timeout * 1e3
        app_log.info("Culling every %i seconds", cull_timeout)
        culler = tornado.ioloop.PeriodicCallback(
            lambda : cull_idle(async_docker_client, proxy_token, delta),
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
