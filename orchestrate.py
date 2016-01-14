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
from tornado.log import app_log
from tornado.web import RequestHandler, HTTPError, RedirectHandler

from tornado import gen, web

import dockworker
import spawnpool


class BaseHandler(RequestHandler):

    # REGEX to test the path specifies a user container
    user_path_regex = re.compile("^/?user/\w+")

    def is_user_path(self, path):
        return path is not None and BaseHandler.user_path_regex.match(path)

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

        # Confirm the client authorization token if an api token is configured
        if self.api_token is not None:
            client_token = self.request.headers.get('Authorization')
            if client_token != 'token %s' % self.api_token:
                return self.send_error(401)

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

    @property
    def api_token(self):
        return self.settings['api_token']
    
class LoadingHandler(BaseHandler):
    def get(self, path=None):
        self.render("loading.html", is_user_path=self.is_user_path(path))


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
            if self.is_user_path(path):
                # Path is trying to get back to a previously existing container

                # Split /user/{some_user}/long/url/path and acquire {some_user}
                path_parts = path.lstrip('/').split('/', 2)
                user = path_parts[1]

                # Scrap a container from the pool and replace it with an ad-hoc replacement.
                # This takes longer, but is necessary to support ad-hoc containers
                yield self.pool.adhoc(user)

                url = path
            else:
                # There is no path or it represents a subpath of the notebook server
                # Assign a prelaunched container from the pool and redirect to it.
                container_path = self.pool.acquire().path
                app_log.info("Allocated [%s] from the pool.", container_path)

                # If no path is set, append self.redirect_uri to the redirect target, else
                # redirect to specified path.

                if path is None:
                    redirect_path = self.redirect_uri
                else:
                    redirect_path = path.lstrip('/')

                url = "/{}/{}".format(container_path, redirect_path)

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

class AdminHandler(RequestHandler):
    def prepare(self):
        '''
        Responds with a 401 error if the configured admin auth token is not 
        present in the request headers.
        '''
        if self.admin_token:
            client_token = self.request.headers.get('Authorization')
            if client_token != 'token %s' % self.admin_token:
                app_log.warn('Rejecting admin request with token %s', client_token)
                return self.send_error(401)
        return super(AdminHandler, self).prepare()

    @property
    def admin_token(self):
        return self.settings['admin_token']

class APIPoolHandler(AdminHandler):
    @gen.coroutine
    def delete(self):
        '''Drains available containers from the pool.'''
        n = yield self.pool.drain()
        app_log.info('Drained pool of %d containers', n)
        self.finish(dict(drained=n))

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
        help="""Host IP address for containers to bind to. If host_network=True,
the host IP address for notebook servers to bind to."""
    )
    tornado.options.define('container_port', default='8888',
        help="""Within container port for notebook servers to bind to. 
If host_network=True, the starting port assigned to notebook servers on the host."""
    )

    command_default = (
        'jupyter notebook --no-browser'
        ' --port {port} --ip=0.0.0.0'
        ' --NotebookApp.base_url=/{base_path}'
        ' --NotebookApp.port_retries=0'
    )

    tornado.options.define('command', default=command_default,
        help="""Command to run when booting the image. A placeholder for 
{base_path} should be provided. A placeholder for {port} and {ip} can be provided."""
    )
    tornado.options.define('port', default=9999,
        help="port for the main server to listen on"
    )
    tornado.options.define('ip', default=None,
        help="ip for the main server to listen on [default: all interfaces]"
    )
    tornado.options.define('admin_port', default=10000,
        help="port for the admin server to listen on"
    )
    tornado.options.define('admin_ip', default='127.0.0.1',
        help="ip for the admin server to listen on [default: 127.0.0.1]"
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
    tornado.options.define('image', default="jupyter/minimal-notebook",
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
    tornado.options.define('host_network', default=False,
        help="""Attaches the containers to the host networking instead of the 
default docker bridge. Affects the semantics of container_port and container_ip."""
    )
    tornado.options.define('host_directories', default=None,
        help="""Mount the specified directory as a data volume, multiple
        directories can be specified by using a comma-delimited string, directory
        path must provided in full (eg: /home/steve/data/:r), permissions default to
        rw""")
    tornado.options.define('user_length', default=12,
        help="Length of the unique /user/:id path generated per container"
    )

    tornado.options.parse_command_line()
    opts = tornado.options.options

    api_token = os.getenv('API_AUTH_TOKEN')
    admin_token = os.getenv('ADMIN_AUTH_TOKEN')
    proxy_token = os.environ['CONFIGPROXY_AUTH_TOKEN']
    proxy_endpoint = os.environ.get('CONFIGPROXY_ENDPOINT', "http://127.0.0.1:8001")
    docker_host = os.environ.get('DOCKER_HOST', 'unix://var/run/docker.sock')

    handlers = [
        (r"/api/spawn/?", APISpawnHandler),
        (r"/api/stats/?", APIStatsHandler)
    ]

    # Only add human-facing handlers if there's no spawn API key set
    if api_token is None:
        handlers.extend([
            (r"/", LoadingHandler),
            (r"/spawn/?(/user/\w+(?:/.*)?)?", SpawnHandler),
            (r"/spawn/((?:notebooks|tree)(?:/.*)?)", SpawnHandler),
            (r"/(user/\w+)(?:/.*)?", LoadingHandler),
            (r"/((?:notebooks|tree)(?:/.*)?)", LoadingHandler),  
            (r"/stats/?", RedirectHandler, {"url": "/api/stats"}),
            (r"/info/?", InfoHandler),
        ])

    admin_handlers = [
        (r"/api/pool/?", APIPoolHandler)
    ]

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
        host_network=opts.host_network,
        host_directories=opts.host_directories
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
                               user_length=opts.user_length
    )

    ioloop = tornado.ioloop.IOLoop().current()

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
        api_token=api_token,
        template_path=os.path.join(os.path.dirname(__file__), 'templates'),
        proxy_endpoint=proxy_endpoint,
        redirect_uri=opts.redirect_uri.lstrip('/'),
    )

    admin_settings = dict(
        pool=pool,
        admin_token=admin_token
    )

    # Cleanup on a fresh state (likely a restart)
    ioloop.run_sync(pool.cleanout)

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

    app_log.info("Admin listening on {}:{}".format(opts.admin_ip or '*', opts.admin_port))
    admin_application = tornado.web.Application(admin_handlers, **admin_settings)
    admin_application.listen(opts.admin_port, opts.admin_ip)

    ioloop.start()

if __name__ == "__main__":
    main()
