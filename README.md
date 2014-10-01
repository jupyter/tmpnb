Launches "temporary" IPython notebook servers.

#### Pre-requisites:

* Docker
* Python 2.7.x
* Node 10.x
* npm

#### Installation

Our bootstrap script is only built for Ubuntu 14.04 currently and must be installed within `/srv/tmpnb`.

```
mkdir -p /srv/
cd /srv/
git clone https://github.com/jupyter/tmpnb.git
cd tmpnb
script/bootstrap
```

The running user needs permission on the Docker socket.

#### Development

```
git clone https://github.com/jupyter/tmpnb.git
cd tmpnb

# If modifying the Docker image in any way
docker build -t jupyter/tmpnb image

pip install -r requirements.txt
npm install jupyter/configurable-http-proxy

# Kick off the proxy and run the server.
# Runs on all interfaces on port 8000 by default.
script/dev
```

#### Contain Yourself

These are working instructions for running everything in Docker (to assist in running on Docker only setups, like CoreOS).

```
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=legit jupyter/configurable-http-proxy --default-target http://127.0.0.1:9999
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=legit -v /var/run/docker.sock:/docker.sock jupyter/tmpnb-orc
```

