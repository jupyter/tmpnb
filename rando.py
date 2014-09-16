#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import json
import os
import socket
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
from tornado.httpclient import HTTPRequest, AsyncHTTPClient

class RandomHandler(RequestHandler):
    @gen.coroutine
    def get(self):
        random_path=base64.urlsafe_b64encode(uuid.uuid4().bytes)

        self.write("Initializing {}".format(random_path))

        port = self.create_notebook_server(random_path)

        app_log.info("Created {} of type {}".format(port, type(port)))
    
        self.proxy(port, random_path)

        # Wait for the notebook server to come up.
        yield self.wait_for_server("127.0.0.1", port)

        # TODO: Fix this fudge factor
        loop = ioloop.IOLoop.current()
        yield gen.Task(loop.add_timeout, loop.time() + .9)

        self.redirect("/" + random_path, permanent=False)

    @gen.coroutine
    def wait_for_server(self, ip, port, timeout=10, wait_time=0.2):
        '''Wait for a server to show up at ip:port'''
        loop = ioloop.IOLoop.current()
        tic = loop.time()
        while loop.time() - tic < timeout:
            try:
                socket.create_connection((ip, port))
            except socket.error as e:
                app_log.info(e)
                self.write(".")
                yield gen.Task(loop.add_timeout, loop.time() + wait_time)
            else:
                break

    @property
    def docker_client(self):
        return self.settings['docker_client']

    @property
    def proxy_token(self):
        return self.settings['proxy_token']

    def create_notebook_server(self, base_path):
        '''
        POST /containers/create
        '''
        # TODO: Use tornado AsyncHTTPClient instead of docker-py

        docker_client = self.docker_client

        env = {"RAND_BASE": base_path}
        container_id = docker_client.create_container('tmpnb')
        container_id = docker_client.create_container(image="tmpnb",
                                                      environment=env)
        docker_client.start(container_id, port_bindings={8888: ('127.0.0.1',)})
        port = docker_client.port(container_id, 8888)[0]['HostPort']

        return int(port)

    @gen.coroutine
    def proxy(self, port, base_path):
        http_client = AsyncHTTPClient()
        headers = {"Authorization": "token {}".format(self.proxy_token)}

        proxy_endpoint = "http://localhost:8001/api/routes/{}".format(base_path)
        body = json.dumps({"target": "http://localhost:{}".format(port)})

        req = HTTPRequest(proxy_endpoint,
                          method="POST",
                          headers=headers,
                          body=body)

        resp = yield http_client.fetch(req)

def main():
    tornado.options.parse_command_line()
    handlers = [(r"/", RandomHandler)]

    docker_client = docker.Client(base_url='unix://var/run/docker.sock',
                                  version='1.12',
                                  timeout=10)

    settings = dict(
        cookie_secret=uuid.uuid4(),
        xsrf_cookies=True,
        debug=True,
        autoescape=None,
        docker_client=docker_client,
        proxy_token=os.environ['CONFIGPROXY_AUTH_TOKEN'],
    )

    port=9999
        
    app_log.info("Listening on {}".format(port))

    application = tornado.web.Application(handlers, **settings)
    application.listen(port)
    tornado.ioloop.IOLoop().instance().start()

if __name__ == "__main__":
    main()
