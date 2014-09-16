#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import json
import os
import uuid

import tornado
import tornado.options
from tornado.log import app_log
from tornado.web import RequestHandler

from tornado import gen, web

from tornado.httputil import url_concat
from tornado.httpclient import HTTPRequest, AsyncHTTPClient

class RandomHandler(RequestHandler):
    def get(self):
        random_path=base64.urlsafe_b64encode(uuid.uuid4().bytes)

        self.write("Initializing {}".format(random_path))

def main():
    tornado.options.parse_command_line()
    handlers = [(r"/", RandomHandler)]

    settings = dict(
        cookie_secret=uuid.uuid4(),
        xsrf_cookies=True,
        debug=True,
        autoescape=None
    )

    port=9999
        
    app_log.info("Listening on {}".format(port))

    application = tornado.web.Application(handlers, **settings)
    application.listen(port)
    tornado.ioloop.IOLoop().instance().start()

if __name__ == "__main__":
    main()
