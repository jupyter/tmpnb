Launches "temporary" IPython notebook servers.

#### Quick start

Get Docker, then:

```
docker pull jupyter/tmpnb
export TOKEN=$( head -c 30 /dev/urandom | xxd -p )
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN jupyter/configurable-http-proxy --default-target http://127.0.0.1:9999
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN -v /var/run/docker.sock:/docker.sock jupyter/tmpnb-orc
```

BAM! Visit your host on port 8000 and you have a working tmpnb setup.

#### Installation

If doing direct installation, you'll need to install it within `/srv/tmpnb` on Ubuntu 14.04.

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
