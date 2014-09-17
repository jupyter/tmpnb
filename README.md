Launches "temporary" IPython notebook servers.

#### Pre-requisites:

* Docker
* Python 2.7.x
* Node 10.x
* npm

#### Installation

```
docker build -t jupyter/tmpnb tmpnb
npm install jupyter/configurable-http-proxy
pip install -r requirements.txt
./launchie.sh
```

The running user needs permission on the Docker socket.
