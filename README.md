Launches "temporary" IPython notebook servers.

#### Pre-requisites:

* Docker
* Python 2.7.x
* Node 10.x
* npm

#### Installation

```
npm install jupyter/configurable-http-proxy
docker build -t tmpnb .
./launchie.sh
```

The running user needs permission on the Docker socket.
