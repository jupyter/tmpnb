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

import docker

import tornado
import tornado.options
from tornado.log import app_log
from tornado.web import RequestHandler

from tornado import gen, web
from tornado import ioloop

from tornado.httputil import url_concat
from tornado.httpclient import HTTPRequest, AsyncHTTPClient, HTTPError

def sample_with_replacement(a, size=12):
    '''Get a random path. If Python had sampling with replacement built in,
    I would use that. The other alternative is numpy.random.choice, but
    numpy is overkill for this tiny bit of random pathing.'''
    return "".join([random.choice(a) for x in range(size)])

@gen.coroutine
def cull_idle(docker_client, containers, proxy_token, delta=None):
    if delta is None:
        delta = datetime.timedelta(minutes=60)
    http_client = AsyncHTTPClient()

    dt = datetime.datetime.utcnow() - delta
    timestamp = dt.isoformat() + 'Z'

    routes_url = "http://localhost:8001/api/routes"

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

    for base_path in data:
        container_id = containers.pop(base_path.lstrip('/'), None)
        if container_id:
            app_log.info("shutting down container %s at %s", container_id, base_path)
            docker_client.kill(container_id)
            docker_client.remove_container(container_id)
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

class IndexHandler(RequestHandler):
    def get(self):
        self.render("index.html")

class RandomHandler(RequestHandler):

    @gen.coroutine
    def get(self):
        random_path = "user-" + sample_with_replacement(string.ascii_letters +
                                                        string.digits)

        self.write("Initializing {}".format(random_path))

        port = self.create_notebook_server(random_path)

        yield self.proxy(port, random_path)

        # Wait for the notebook server to come up.
        yield self.wait_for_server("127.0.0.1", port)

        loop = ioloop.IOLoop.current()
        yield gen.Task(loop.add_timeout, loop.time() + 1.1)

        self.redirect("/" + random_path + "/tree", permanent=False)

    @gen.coroutine
    def wait_for_server(self, ip, port, timeout=10, wait_time=0.2):
        '''Wait for a server to show up at ip:port'''
        loop = ioloop.IOLoop.current()
        tic = loop.time()
        while loop.time() - tic < timeout:
            try:
                socket.create_connection((ip, port))
            except socket.error as e:
                if e.errno != errno.ECONNREFUSED:
                    app_log.warn("Error attempting to connect to %s:%i - %s",
                        ip, port, e,
                    )
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

    def create_notebook_server(self, base_path):
        '''
        POST /containers/create
        '''
        # TODO: Use tornado AsyncHTTPClient instead of docker-py

        docker_client = self.docker_client

        env = {"RAND_BASE": base_path}
        container_id = docker_client.create_container(image="jupyter/tmpnb",
                                                      environment=env)
        docker_client.start(container_id, port_bindings={8888: (self.container_ip,)})
        port = docker_client.port(container_id, 8888)[0]['HostPort']

        self.settings['containers'][base_path] = container_id

        return int(port)

    @gen.coroutine
    def proxy(self, port, base_path):
        http_client = AsyncHTTPClient()
        headers = {"Authorization": "token {}".format(self.proxy_token)}

        proxy_endpoint = self.proxy_endpoint + "/api/routes/{}".format(base_path)
        body = json.dumps({"target": "http://localhost:{}".format(port)})

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
    tornado.options.parse_command_line()
    opts = tornado.options.options

    handlers = [
        (r"/", IndexHandler),
        (r"/random", RandomHandler),
    ]

    proxy_token = os.environ['CONFIGPROXY_AUTH_TOKEN']
    proxy_endpoint = os.environ.get('CONFIGPROXY_ENDPOINT', "http://localhost:8001")
    docker_host = os.environ.get('DOCKER_HOST', 'unix://var/run/docker.sock')
    
    containers = {}

    docker_client = docker.Client(base_url=docker_host,
                                  version='1.12',
                                  timeout=10)

    settings = dict(
        cookie_secret=uuid.uuid4(),
        xsrf_cookies=True,
        debug=True,
        autoescape=None,
        docker_client=docker_client,
        container_ip = opts.container_ip,
        containers=containers,
        proxy_token=proxy_token,
        template_path=os.path.join(os.path.dirname(__file__), 'templates'),
        proxy_endpoint=proxy_endpoint,
    )
    
    # check for idle containers and cull them
    cull_timeout = opts.cull_timeout
    
    if cull_timeout:
        delta = datetime.timedelta(seconds=cull_timeout)
        cull_ms = cull_timeout * 1e3
        app_log.info("Culling every %i seconds", cull_timeout)
        culler = tornado.ioloop.PeriodicCallback(
            lambda : cull_idle(docker_client, containers, proxy_token, delta),
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
