import datetime
import json

from tornado.log import app_log

from tornado import gen, web
from tornado.httputil import url_concat
from tornado.httpclient import HTTPRequest, HTTPError, AsyncHTTPClient

AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")

@gen.coroutine
def cull_idle(docker_client, proxy_token, delta=None):
    if delta is None:
        delta = datetime.timedelta(minutes=60)
    http_client = AsyncHTTPClient()

    dt = datetime.datetime.utcnow() - delta
    timestamp = dt.isoformat() + 'Z'

    routes_url = "http://127.0.0.1:8001/api/routes"

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
                docker_client.kill(container_id)
                docker_client.remove_container(container_id)
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
