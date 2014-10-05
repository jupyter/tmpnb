import datetime
import json

from tornado.log import app_log

from tornado import gen, web
from tornado.httputil import url_concat
from tornado.httpclient import HTTPRequest, HTTPError, AsyncHTTPClient

AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")

class SpawnPool():
    def __init__(self, docker_client, ipython_executable):
        self.docker_client = docker_client
        self.ipython_executable = ipython_executable


    @gen.coroutine
    def create_notebook_server(self, base_path,
                               image="jupyter/demo",
                               ipython_executable="ipython3",
                               mem_limit="512m",
                               cpu_shares="1",
                               container_ip="127.0.0.1",
                               container_port=8888):
        '''
        Creates a notebook_server running off of `base_path`.

        returns the container_id, ip, port in a Future
        '''
        docker_client = self.docker_client

        ipython_args = [
                "notebook", "--no-browser",
                "--port {}".format(container_port),
                "--ip=0.0.0.0",
                "--NotebookApp.base_url=/{}".format(base_path),
                "--NotebookApp.tornado_settings=\"{'template_path':['/srv/ga/', '/srv/ipython/IPython/html', '/srv/ipython/IPython/html/templates']}\""
        ]

        ipython_command = ipython_executable + " " + " ".join(ipython_args)

        command = [
            "/bin/sh",
            "-c",
            ipython_command
        ]

        resp = yield docker_client.create_container(image=image,
                                                    command=command,
                                                    mem_limit=mem_limit,
                                                    cpu_shares=cpu_shares)

        docker_warnings = resp['Warnings']
        if docker_warnings is not None:
            app_log.warn(docker_warnings)

        container_id = resp['Id']
        app_log.info("Created container {}".format(container_id))

        yield docker_client.start(container_id,
                                  port_bindings={container_port: (container_ip,)})

        container_network = yield docker_client.port(container_id,
                                                     container_port)

        host_port = container_network[0]['HostPort']
        host_ip = container_network[0]['HostIp']

        raise gen.Return((container_id, host_ip, int(host_port)))

@gen.coroutine
def cull_idle(docker_client, proxy_endpoint, proxy_token, delta=None):
    if delta is None:
        delta = datetime.timedelta(minutes=60)
    http_client = AsyncHTTPClient()

    dt = datetime.datetime.utcnow() - delta
    timestamp = dt.isoformat() + 'Z'

    routes_url = proxy_endpoint + "/api/routes"

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

    for base_path, route in data.items():
        container_id = route.get('container_id', None)
        if container_id:
            app_log.info("shutting down container %s at %s", container_id, base_path)
            try:
                yield docker_client.kill(container_id)
                yield docker_client.remove_container(container_id)
            except Exception as e:
                app_log.error("Unable to cull {}: {}".format(container_id, e))
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
