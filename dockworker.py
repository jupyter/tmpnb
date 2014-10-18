from concurrent.futures import ThreadPoolExecutor
from collections import namedtuple

import docker

from tornado.log import app_log

from tornado import gen, web


ContainerConfig = namedtuple('ContainerConfig', [
    'image', 'ipython_executable', 'mem_limit', 'cpu_shares', 'container_ip', 'container_port'
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
    def create_notebook_server(self, base_path, container_config):
        '''Creates a notebook_server running off of `base_path`.

        Returns the container_id, ip, port in a Future.'''

        templates = ['/srv/ga',
                     '/srv/ipython/IPython/html',
                     '/srv/ipython/IPython/html/templates']

        tornado_settings = {'template_path': templates}

        ipython_args = [
                "notebook", "--no-browser",
                "--port {}".format(container_config.container_port),
                "--ip=0.0.0.0",
                "--NotebookApp.base_url=/{}".format(base_path),
                "--NotebookApp.tornado_settings=\"{}\"".format(tornado_settings)
        ]

        ipython_command = container_config.ipython_executable + " " + " ".join(ipython_args)

        command = [
            "/bin/sh",
            "-c",
            ipython_command
        ]

        resp = yield self._with_retries(RETRIES,
                                        self.docker_client.create_container,
                                        image=container_config.image,
                                        command=command,
                                        mem_limit=container_config.mem_limit,
                                        cpu_shares=container_config.cpu_shares)

        docker_warnings = resp['Warnings']
        if docker_warnings is not None:
            app_log.warn(docker_warnings)

        container_id = resp['Id']
        app_log.info("Created container {}".format(container_id))

        port_bindings = {
            container_config.container_port: (container_config.container_ip,)
        }
        yield self._with_retries(RETRIES,
                                 self.docker_client.start,
                                 container_id,
                                 port_bindings=port_bindings)

        container_network = yield self._with_retries(RETRIES,
                                                     self.docker_client.port,
                                                     container_id,
                                                     container_config.container_port)

        host_port = container_network[0]['HostPort']
        host_ip = container_network[0]['HostIp']

        raise gen.Return((container_id, host_ip, int(host_port)))

    @gen.coroutine
    def shutdown_notebook_server(self, container_id, alive=True):
        '''Gracefully stop a running container.'''

        if alive:
            yield self._with_retries(RETRIES, self.docker_client.stop, container_id)
        yield self._with_retries(RETRIES, self.docker_client.remove_container, container_id)

    @gen.coroutine
    def list_notebook_servers(self, container_config, all=True):
        '''List containers that were launched from a specific image.'''

        existing = yield self._with_retries(RETRIES,
                                            self.docker_client.containers,
                                            all=all,
                                            trunc=False)

        untagged_image = container_config.image.split(':')[0]
        def has_matching_image(container):
            return container['Image'].split(':')[0] == untagged_image

        matching = [container for container in existing if has_matching_image(container)]
        raise gen.Return(matching)

    @gen.coroutine
    def _with_retries(self, max_tries, fn, *args, **kwargs):
        '''Attempt a Docker API call.

        If an error occurs, retry up to "max_tries" times before letting the exception propagate
        up the stack.'''

        try:
            result = yield fn(*args, **kwargs)
            raise gen.Return(result)
        except docker.errors.APIError as e:
            app_log.error("Encountered a Docker error (%i retries remain): %s", max_tries, e)
            if max_tries > 0:
                result = yield self._with_retries(max_tries - 1, fn, *args, **kwargs)
                raise gen.Return(result)
            else:
                raise e
