Launches "temporary" IPython notebook servers.

#### Pre-requisites:

* Docker
* Python 2.7.x
* Node 10.x
* npm

#### Installation

Our bootstrap script is only built for Ubuntu 14.04 currently.

```
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
