import itertools
import re

from concurrent.futures import ThreadPoolExecutor
from collections import namedtuple

import docker

from tornado.log import app_log

from tornado import gen, web

class ContainerConfig():
    def __init__(self, image='jupyter/demo',
                 mem_limit='512m',
                 cpu_shares=None,
                 command="ipython notebook --base-url={base_url}",
                 container_ip="127.0.0.1",
                 container_port="8888",
                 volumes=None,
                 read_only_volumes=None):

        self.image = image
        self.mem_limit = mem_limit
        self.cpu_shares = cpu_shares
        self.command = command
        self.container_ip = container_ip
        self.container_port = container_port

        if(volumes is None):
            volumes = {}
        if(read_only_volumes is None):
            read_only_volumes = {}
        
        self.volumes = volumes
        self.read_only_volumes = read_only_volumes

        self.volume_mount_points = list(
                itertools.chain(
                    self.volumes.values(),
                    self.read_only_volumes.values()
                )
        )

        binds = {}
        binds.update({
            host_fs_path: {'bind': container_fs_path, 'ro': False}
            for host_fs_path, container_fs_path in self.volumes.items()
        
        })
        binds.update({
            host_fs_path: {'bind': container_fs_path, 'ro': True}
            for host_fs_path, container_fs_path in self.read_only_volumes.items()
        
        })

        self.volume_binds = binds
        

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
                 version='1.12',
                 timeout=30,
                 max_workers=64):

        blocking_docker_client = docker.Client(base_url=docker_host,
                                               version=version,
                                               timeout=timeout)

        executor = ThreadPoolExecutor(max_workers=max_workers)

        async_docker_client = AsyncDockerClient(blocking_docker_client,
                                                executor)
        self.docker_client = async_docker_client

    @gen.coroutine
    def create_notebook_server(self, base_path, name, container_config):
        '''Creates a notebook_server running off of `base_path`.

        Returns the (container_id, ip, port) tuple in a Future.'''

        port = container_config.container_port

        app_log.debug(container_config)

        # Assumes that the container_config.command is of a format like:
        #
        #  ipython3 notebook --no-browser --port {port} --ip=0.0.0.0
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

        resp = yield self._with_retries(self.docker_client.create_container,
                                        image=container_config.image,
                                        command=command,
                                        mem_limit=container_config.mem_limit,
                                        cpu_shares=container_config.cpu_shares,
                                        name=name)

        docker_warnings = resp['Warnings']
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
            for name in container['Names']:
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
        except docker.errors.APIError as e:
            app_log.error("Encountered a Docker error (%i retries remain): %s", max_tries, e)
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
