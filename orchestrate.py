#!/usr/bin/env python
# -*- coding: utf-8 -*-

import datetime
import json
import os
import re
import uuid

from concurrent.futures import ThreadPoolExecutor

import tornado
import tornado.options
from tornado import web
from tornado.log import app_log
from tornado.web import RequestHandler, HTTPError, RedirectHandler

from tornado import gen, web

import dockworker
import spawnpool

class BaseHandler(RequestHandler):
    def write_error(self, status_code, **kwargs):
        if status_code == 404:
            self.render("error/404.html", status_code = status_code)
        else:
            self.render("error/500.html", status_code = status_code)

    def prepare(self):
        if self.allow_origin:
            self.set_header("Access-Control-Allow-Origin", self.allow_origin)
        if self.expose_headers:
            self.set_header("Access-Control-Expose-Headers", self.expose_headers)
        if self.max_age:
            self.set_header("Access-Control-Max-Age", self.max_age)
        if self.allow_credentials:
            self.set_header("Access-Control-Allow-Credentials", self.allow_credentials)
        if self.allow_methods:
            self.set_header("Access-Control-Allow-Methods", self.allow_methods)
        if self.allow_headers:
            self.set_header("Access-Control-Allow-Headers", self.allow_headers)

    @property
    def allow_origin(self):
        return self.settings['allow_origin']

    @property
    def expose_headers(self):
        return self.settings['expose_headers']

    @property
    def max_age(self):
        return self.settings['max_age']

    @property
    def allow_credentials(self):
        return self.settings['allow_credentials']

    @property
    def allow_methods(self):
        return self.settings['allow_methods']

    @property
    def allow_headers(self):
        return self.settings['allow_headers']

    def get_json_body(self):
        """Return the body of the request as JSON data."""
        if not self.request.body:
            return None
        body = self.request.body.strip().decode(u'utf-8')
        try:
            model = json.loads(body)
        except Exception:
            self.log.debug("Bad JSON: %r", body)
            self.log.error("Couldn't parse JSON", exc_info=True)
            raise web.HTTPError(400, u'Invalid JSON in body of request')
        return model

class LoadingHandler(BaseHandler):
    def get(self, path=None):
        self.render("loading.html", path=path)


class APIStatsHandler(BaseHandler):
    def get(self):
        '''Returns some statistics/metadata about the tmpnb server'''
        self.set_header("Content-Type", 'application/json')
        response = {
                'available': len(self.pool.available),
                'capacity': self.pool.capacity,
                'version': '0.1.0',
                'container_image': self.pool.container_config.image,
        }
        self.write(response)

    @property
    def pool(self):
        return self.settings['pool']


class InfoHandler(BaseHandler):
    def get(self):
        self.render("stats.html")

    @property
    def pool(self):
        return self.settings['pool']

class SpawnHandler(BaseHandler):

    @gen.coroutine
    def get(self, path=None):
        '''Spawns a brand new server'''
        try:
            if path is None:
                # No path. Assign a prelaunched container from the pool and redirect to it.
                # Append self.redirect_uri to the redirect target.
                container_path = self.pool.acquire().path
                app_log.info("Allocated [%s] from the pool.", container_path)

                url = "/{}/{}".format(container_path, self.redirect_uri)
            else:
                # Split /user/{some_user}/long/url/path and acquire {some_user}
                path_parts = path.lstrip('/').split('/', 2)
                user = path_parts[1]

                # Scrap a container from the pool and replace it with an ad-hoc replacement.
                # This takes longer, but is necessary to support ad-hoc containers
                yield self.pool.adhoc(user)

                url = path

            app_log.debug("Redirecting [%s] -> [%s].", self.request.path, url)
            self.redirect(url, permanent=False)
        except spawnpool.EmptyPoolError:
            app_log.warning("The container pool is empty!")
            self.render("full.html", cull_period=self.cull_period)

    @property
    def pool(self):
        return self.settings['pool']

    @property
    def cull_period(self):
        return self.settings['cull_period']

    @property
    def redirect_uri(self):
        return self.settings['redirect_uri']


class APISpawnHandler(BaseHandler):

    @gen.coroutine
    def post(self):
        '''Spawns a brand new server programatically'''
        try:
            url = self.pool.acquire().path
            app_log.info("Allocated [%s] from the pool.", url)
            app_log.debug("Responding with container url [%s].", url)
            self.write({'url': url})
        except spawnpool.EmptyPoolError:
            app_log.warning("The container pool is empty!")
            self.write({'status': 'full'})

    @property
    def pool(self):
        return self.settings['pool']

class APIPoolHandler(BaseHandler):
    #@web.authenticated
    @gen.coroutine
    def post(self):
        '''Allow updating pool configuration remotely'''
        # TODO: Auth
        # TODO: Determine payload format
        model = self.get_json_body()
        if model is None:
            return self.write({'error': 'empty payload'})

        if 'pool' in model and 'capacity' in model['pool']:
            new_capacity = int(model['pool']['capacity'])
            self.pool.capacity = new_capacity
            yield self.pool.heartbeat()

        diagnosis = self.pool.diagnosis()
        routes = yield diagnosis._proxy_routes()

        print(routes)

        self.write(dict(
            capacity=self.pool.capacity,
            navailable=len(self.pool.available),
            routes=routes,
        ))


    @property
    def pool(self):
        return self.settings['pool']

def main():
    tornado.options.define('cull_period', default=600,
        help="Interval (s) for culling idle containers."
    )
    tornado.options.define('cull_timeout', default=3600,
        help="Timeout (s) for culling idle containers."
    )
    tornado.options.define('container_ip', default='127.0.0.1',
        help="IP address for containers to bind to"
    )
    tornado.options.define('container_port', default='8888',
        help="Port for containers to bind to"
    )

    command_default = (
        'ipython notebook --no-browser'
        ' --port {port} --ip=0.0.0.0'
        ' --NotebookApp.base_url=/{base_path}'
    )

    tornado.options.define('command', default=command_default,
        help="command to run when booting the image. A placeholder for base_path should be provided."
    )
    tornado.options.define('port', default=9999,
        help="port for the main server to listen on"
    )
    tornado.options.define('ip', default=None,
        help="ip for the main server to listen on [default: all interfaces]"
    )
    tornado.options.define('max_dock_workers', default=2,
        help="Maximum number of docker workers"
    )
    tornado.options.define('mem_limit', default="512m",
        help="Limit on Memory, per container"
    )
    tornado.options.define('cpu_shares', default=None,
        help="Limit CPU shares, per container"
    )
    tornado.options.define('image', default="jupyter/minimal",
        help="Docker container to spawn for new users. Must be on the system already"
    )
    tornado.options.define('docker_version', default="auto",
        help="Version of the Docker API to use"
    )
    tornado.options.define('redirect_uri', default="/tree",
        help="URI to redirect users to upon initial notebook launch"
    )
    tornado.options.define('pool_size', default=10,
        help="Capacity for containers on this system. Will be prelaunched at startup."
    )
    tornado.options.define('pool_name', default=None,
        help="Container name fragment used to identity containers that belong to this instance."
    )
    tornado.options.define('static_files', default=None,
        help="Static files to extract from the initial container launch"
    )
    tornado.options.define('allow_origin', default=None,
        help="Set the Access-Control-Allow-Origin header. Use '*' to allow any origin to access."
    )
    tornado.options.define('expose_headers', default=None,
            help="Sets the Access-Control-Expose-Headers header."
    )
    tornado.options.define('max_age', default=None,
        help="Sets the Access-Control-Max-Age header."
    )
    tornado.options.define('allow_credentials', default=None,
        help="Sets the Access-Control-Allow-Credentials header."
    )
    tornado.options.define('allow_methods', default=None,
        help="Sets the Access-Control-Allow-Methods header."
    )
    tornado.options.define('allow_headers', default=None,
        help="Sets the Access-Control-Allow-Headers header."
    )
    tornado.options.define('assert_hostname', default=False,
        help="Verify hostname of Docker daemon."
    )
    tornado.options.define('container_user', default=None,
        help="User to run container command as"
    )

    tornado.options.parse_command_line()
    opts = tornado.options.options

    handlers = [
        (r"/", LoadingHandler),
        (r"/spawn/?(/user/\w+(?:/.*)?)?", SpawnHandler),
        (r"/api/spawn/?", APISpawnHandler),
        (r"/(user/\w+)(?:/.*)?", LoadingHandler),
        (r"/api/stats/?", APIStatsHandler),
        (r"/stats/?", RedirectHandler, {"url": "/api/stats"}),
        (r"/info/?", InfoHandler),
        (r"/api/pool/1?", APIPoolHandler),
    ]

    proxy_token = os.environ['CONFIGPROXY_AUTH_TOKEN']
    proxy_endpoint = os.environ.get('CONFIGPROXY_ENDPOINT', "http://127.0.0.1:8001")
    docker_host = os.environ.get('DOCKER_HOST', 'unix://var/run/docker.sock')

    max_age = datetime.timedelta(seconds=opts.cull_timeout)
    pool_name = opts.pool_name
    if pool_name is None:
        # Derive a valid container name from the image name by default.
        pool_name = re.sub('[^a-zA-Z0_.-]+', '', opts.image.split(':')[0])

    container_config = dockworker.ContainerConfig(
        image=opts.image,
        command=opts.command,
        mem_limit=opts.mem_limit,
        cpu_shares=opts.cpu_shares,
        container_ip=opts.container_ip,
        container_port=opts.container_port,
        container_user=opts.container_user,
    )

    spawner = dockworker.DockerSpawner(docker_host,
                                       timeout=30,
                                       version=opts.docker_version,
                                       max_workers=opts.max_dock_workers,
                                       assert_hostname=opts.assert_hostname,
    )

    static_path = os.path.join(os.path.dirname(__file__), "static")

    pool = spawnpool.SpawnPool(proxy_endpoint=proxy_endpoint,
                               proxy_token=proxy_token,
                               spawner=spawner,
                               container_config=container_config,
                               capacity=opts.pool_size,
                               max_age=max_age,
                               static_files=opts.static_files,
                               static_dump_path=static_path,
                               pool_name=pool_name,
    )

    ioloop = tornado.ioloop.IOLoop().instance()

    settings = dict(
        default_handler_class=BaseHandler,
        static_path=static_path,
        cookie_secret=uuid.uuid4(),
        xsrf_cookies=False,
        debug=True,
        cull_period=opts.cull_period,
        allow_origin=opts.allow_origin,
        expose_headers=opts.expose_headers,
        max_age=opts.max_age,
        allow_credentials=opts.allow_credentials,
        allow_methods=opts.allow_methods,
        allow_headers=opts.allow_headers,
        spawner=spawner,
        pool=pool,
        autoescape=None,
        proxy_token=proxy_token,
        template_path=os.path.join(os.path.dirname(__file__), 'templates'),
        proxy_endpoint=proxy_endpoint,
        redirect_uri=opts.redirect_uri.lstrip('/'),
    )

    # Synchronously cull any existing, inactive containers, and pre-launch a set number of
    # containers, ready to serve.
    ioloop.run_sync(pool.heartbeat)

    if(opts.static_files):
        ioloop.run_sync(pool.copy_static)

    # Periodically execute a heartbeat function to cull used containers and regenerated failed
    # ones, self-healing the cluster.
    cull_ms = opts.cull_period * 1e3
    app_log.info("Culling containers unused for %i seconds every %i seconds.",
                 opts.cull_timeout,
                 opts.cull_period)
    culler = tornado.ioloop.PeriodicCallback(pool.heartbeat, cull_ms)
    culler.start()

    app_log.info("Listening on {}:{}".format(opts.ip or '*', opts.port))
    application = tornado.web.Application(handlers, **settings)
    application.listen(opts.port, opts.ip)
    ioloop.start()

if __name__ == "__main__":
    main()
