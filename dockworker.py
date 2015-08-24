from concurrent.futures import ThreadPoolExecutor
from collections import namedtuple
import re

import docker
import requests

from docker.utils import create_host_config, kwargs_from_env

from tornado import gen, web
from tornado.log import app_log

ContainerConfig = namedtuple('ContainerConfig', [
    'image', 'command', 'mem_limit', 'cpu_shares', 'container_ip', 'container_port'
])

# Number of times to retry API calls before giving up.
RETRIES = 5


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
                 version='1.18',
                 timeout=30,
                 max_workers=64):

        kwargs = kwargs_from_env(assert_hostname=False)

        # environment variable DOCKER_HOST takes precedence
        kwargs.setdefault('base_url', docker_host)

        blocking_docker_client = docker.Client(version=version,
                                               timeout=timeout,
                                               **kwargs)

        executor = ThreadPoolExecutor(max_workers=max_workers)

        async_docker_client = AsyncDockerClient(blocking_docker_client,
                                                executor)
        self.docker_client = async_docker_client

    @gen.coroutine
    def create_notebook_server(self, base_path, container_name, container_config):
        '''Creates a notebook_server running off of `base_path`.

        Returns the (container_id, ip, port) tuple in a Future.'''

        port = container_config.container_port

        app_log.debug(container_config)

        # Assumes that the container_config.command is of a format like:
        #
        #  ipython notebook --no-browser --port {port} --ip=0.0.0.0
        #    --NotebookApp.base_path=/{base_path}
        #    --NotebookApp.tornado_settings=\"{ \"template_path\": [ \"/srv/ga\",
        #    \"/srv/ipython/IPython/html\",
        #    \"/srv/ipython/IPython/html/templates\" ] }\""
        #
        # Important piece here is the parametrized base_path to let the
        # underlying process know where the proxy is routing it.
        rendered_command = container_config.command.format(base_path=base_path, port=port)

        command = [
            "/bin/sh",
            "-c",
            rendered_command
        ]

        host_config = dict(
            mem_limit=container_config.mem_limit
        )

        host_config = create_host_config(**host_config)

        resp = yield self._with_retries(self.docker_client.create_container,
                                        image=container_config.image,
                                        command=command,
                                        host_config=host_config,
                                        cpu_shares=int(container_config.cpu_shares),
                                        name=container_name)

        app_log.info(resp)
        docker_warnings = resp.get('Warnings')
        if docker_warnings is not None:
            app_log.warn(docker_warnings)

        container_id = resp['Id']
        app_log.info("Created container {}".format(container_id))

        port_bindings = {
            container_config.container_port: (container_config.container_ip,)
        }
        yield self._with_retries(self.docker_client.start,
                                 container_id,
                                 port_bindings=port_bindings)

        container_network = yield self._with_retries(self.docker_client.port,
                                                     container_id,
                                                     container_config.container_port)

        host_port = container_network[0]['HostPort']
        host_ip = container_network[0]['HostIp']

        raise gen.Return((container_id, host_ip, int(host_port)))

    @gen.coroutine
    def shutdown_notebook_server(self, container_id, alive=True):
        '''Gracefully stop a running container.'''

        if alive:
            yield self._with_retries(self.docker_client.stop, container_id)
        yield self._with_retries(self.docker_client.remove_container, container_id)

    @gen.coroutine
    def list_notebook_servers(self, pool_regex, all=True):
        '''List containers that are managed by a specific pool.'''

        existing = yield self._with_retries(self.docker_client.containers,
                                            all=all,
                                            trunc=False)

        def name_matches(container):
            try:
                names = container['Names']
                if names is None:
                  app_log.warn("Docker API returned null Names, ignoring")
                  return False
            except Exception:
                app_log.warn("Invalid container: %r", container)
                return False
            for name in names:
                if pool_regex.search(name):
                    return True
            return False

        matching = [container for container in existing if name_matches(container)]
        raise gen.Return(matching)

    @gen.coroutine
    def _with_retries(self, fn, *args, **kwargs):
        '''Attempt a Docker API call.

        If an error occurs, retry up to "max_tries" times before letting the exception propagate
        up the stack.'''

        max_tries = kwargs.get('max_tries', RETRIES)
        try:
            if 'max_tries' in kwargs:
                del kwargs['max_tries']
            result = yield fn(*args, **kwargs)
            raise gen.Return(result)
        except (docker.errors.APIError, requests.exceptions.RequestException) as e:
            app_log.error("Encountered a Docker error with {} ({} retries remain): {}".format(fn.__name__, max_tries, e))
            if max_tries > 0:
                kwargs['max_tries'] = max_tries - 1
                result = yield self._with_retries(fn, *args, **kwargs)
                raise gen.Return(result)
            else:
                raise e

    @gen.coroutine
    def copy_files(self, container_id, path):
        '''Returns a tarball of path from container_id'''
        tarball = yield self.docker_client.copy(container_id, path)
        raise gen.Return(tarball)
