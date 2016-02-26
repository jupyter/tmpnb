## tmpnb, the temporary notebook service

[![Gitter](https://badges.gitter.im/Join Chat.svg)](https://gitter.im/jupyter/tmpnb?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge)

Launches "temporary" Jupyter notebook servers.

![tmpnb architecture](https://cloud.githubusercontent.com/assets/836375/5911140/c53e3978-a587-11e4-86a5-695469ef23a5.png)

tmpnb launches a docker container for each user that requests one. In practice, this gets used to [provide temporary notebooks](https://tmpnb.org), demo the IPython notebook as part of [a Nature article](http://www.nature.com/news/interactive-notebooks-sharing-the-code-1.16261), or even [provide Jupyter kernels for publications](http://odewahn.github.io/publishing-workflows-for-jupyter/#1).

People have used it at user groups, meetups, and workshops to provide temporary access to a full system without any installation whatsoever.

#### Quick start

Get Docker, then:

```
docker pull jupyter/minimal-notebook
export TOKEN=$( head -c 30 /dev/urandom | xxd -p )
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN --name=proxy jupyter/configurable-http-proxy --default-target http://127.0.0.1:9999
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN --name=tmpnb -v /var/run/docker.sock:/docker.sock jupyter/tmpnb
```

BAM! Visit your Docker host on port 8000 and you have a working tmpnb setup. The ` -v /var/run/docker.sock:/docker.sock` bit causes the orchestrator container to mount the docker client, which allows the orchestrator container to spawn docker containers on the host (see [this article](http://nathanleclaire.com/blog/2014/07/12/10-docker-tips-and-tricks-that-will-make-you-sing-a-whale-song-of-joy/#bind-mount-the-docker-socket-on-docker-run:1765430f0793020845eca6c8326a4e45) for more information).

If you are running docker using docker-machine, as is now the standard, get the IP address of your Docker host by running `docker-machine ls`. If you are using boot2docker, then you can find your docker host's ip address by running the following command in your console: `boot2docker ip`

If it didn't come up, try running `docker ps -a` and `docker logs tmpnb` to help diagnose issues.

#### Advanced configuration

If you need to set the `docker-version` or other options, they can be passed to `jupyter/tmpnb` directly:

```
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN -v /var/run/docker.sock:/docker.sock jupyter/tmpnb python orchestrate.py --cull-timeout=60 --docker-version="1.13" --command="jupyter notebook --NotebookApp.base_url={base_path} --ip=0.0.0.0 --port {port}"
```

Note that if you do not pass a value to `docker-version`, tmpnb will automatically use the Docker API version provided by the server.

The tmpnb server has two APIs: a public one that receives HTTP requests under the `/` proxy route and an administrative one available only on the private, localhost interface by default. You can configure the interfaces (`--ip`, `--admin_ip`) and ports (`--port`, `--admin_port`) of both APIs using command line arguments.

If you decide to expose the admin API on a public interface, you can protect it by specifying a secret token in the environment variable `ADMIN_AUTH_TOKEN` when starting the `tmpnb` container. Thereafter, all requests made to the admin API must include it in an HTTP header like so:

```
Authorization: token <admin secret token here>
```

Likewise, if you only want to allow programmatic access to your tmpnb cluster by select clients, you can specify a separate secret token in the environment variable `API_AUTH_TOKEN` when starting the `tmpnb` container. All requests made to the public API must include it in an HTTP header in the same manner as depicted for the admin token above. Note that when this token is set, only the `/api/*` resources of the tmpnb server are available. All human-facing paths are disabled.

If you want to see the resources available in both the admin and user APIs, look at the handler paths registered in the `orchestrate.py` file. You should consider both APIs to be unstable.

#### Launching with *your own* Docker images

tmpnb can run any Docker container provided by the `--image` option, so long as the `--command` option tells where the `{base_path}` and `{port}`. Those are literal strings, complete with curly braces that tmpnb will replace with an assigned `base_path` and `port`.

```
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN \
           -v /var/run/docker.sock:/docker.sock \
           jupyter/tmpnb python orchestrate.py --image='jupyter/demo' --command="jupyter notebook --NotebookApp.base_url={base_path} --ip=0.0.0.0 --port {port}"
```

#### Using [jupyter/docker-stacks](https://github.com/jupyter/docker-stacks) images

When using the latest [jupyter/docker-stacks](https://github.com/jupyter/docker-stacks) images with tmpnb, you can use the `start-notebook.sh` script or invoke the `jupyter notebook` command directly to run your notebook servers as user `jovyan`. Substitute your desired docker-stacks image name in the command below.

```bash
docker run -d \
    --net=host \
    -e CONFIGPROXY_AUTH_TOKEN=$TOKEN \
    -v /var/run/docker.sock:/docker.sock \
    jupyter/tmpnb \
    python orchestrate.py --image='jupyter/minimal-notebook' \
        --command='start-notebook.sh \
            "--NotebookApp.base_url={base_path} \
            --ip=0.0.0.0 \
            --port={port} \
            --NotebookApp.trust_xheaders=True"'
```

#### Options

```
Usage: orchestrate.py [OPTIONS]

orchestrate.py options:

  --admin-ip                       ip for the admin server to listen on
                                   [default: 127.0.0.1] (default 127.0.0.1)
  --admin-port                     port for the admin server to listen on
                                   (default 10000)
  --allow-credentials              Sets the Access-Control-Allow-Credentials
                                   header.
  --allow-headers                  Sets the Access-Control-Allow-Headers
                                   header.
  --allow-methods                  Sets the Access-Control-Allow-Methods
                                   header.
  --allow-origin                   Set the Access-Control-Allow-Origin header.
                                   Use '*' to allow any origin to access.
  --assert-hostname                Verify hostname of Docker daemon. (default
                                   False)
  --command                        Command to run when booting the image. A
                                   placeholder for  {base_path} should be
                                   provided. A placeholder for {port} and {ip}
                                   can be provided. (default jupyter notebook
                                   --no-browser --port {port} --ip=0.0.0.0
                                   --NotebookApp.base_url=/{base_path}
                                   --NotebookApp.port_retries=0)
  --container-ip                   Host IP address for containers to bind to.
                                   If host_network=True, the host IP address
                                   for notebook servers to bind to. (default
                                   127.0.0.1)
  --container-port                 Within container port for notebook servers
                                   to bind to.  If host_network=True, the
                                   starting port assigned to notebook servers
                                   on the host. (default 8888)
  --container-user                 User to run container command as
  --cpu-shares                     Limit CPU shares, per container
  --cull-period                    Interval (s) for culling idle containers.
                                   (default 600)
  --cull-timeout                   Timeout (s) for culling idle containers.
                                   (default 3600)
  --docker-version                 Version of the Docker API to use (default
                                   auto)
  --expose-headers                 Sets the Access-Control-Expose-Headers
                                   header.
  --extra-hosts                    Extra hosts for the containers, multiple
                                   hosts can be specified         by using a
                                   comma-delimited string, specified in the
                                   form hostname:IP (default [])
  --host-directories               Mount the specified directory as a data
                                   volume, multiple         directories can be
                                   specified by using a comma-delimited string,
                                   directory         path must provided in full
                                   (eg: /home/steve/data/:r), permissions
                                   default to         rw
  --host-network                   Attaches the containers to the host
                                   networking instead of the  default docker
                                   bridge. Affects the semantics of
                                   container_port and container_ip. (default
                                   False)
  --image                          Docker container to spawn for new users.
                                   Must be on the system already (default
                                   jupyter/minimal)
  --ip                             ip for the main server to listen on
                                   [default: all interfaces]
  --max-age                        Sets the Access-Control-Max-Age header.
  --max-dock-workers               Maximum number of docker workers (default 2)
  --mem-limit                      Limit on Memory, per container (default
                                   512m)
  --pool-name                      Container name fragment used to identity
                                   containers that belong to this instance.
  --pool-size                      Capacity for containers on this system. Will
                                   be prelaunched at startup. (default 10)
  --port                           port for the main server to listen on
                                   (default 9999)
  --redirect-uri                   URI to redirect users to upon initial
                                   notebook launch (default /tree)
  --static-files                   Static files to extract from the initial
                                   container launch
  --user_length                    Length of the unique /user/:id path
                                   generated per container (default 12)
```

#### Development

*WARNING* The `Makefile` used in the commands below assume your
containers can be deleted.  Please work on an isolated machine and read
the `cleanup` target in the `Makefile` prior to executing.

```
git clone https://github.com/jupyter/tmpnb.git
cd tmpnb

# Kick off the proxy and run the server.
# Runs on all interfaces on port 8000 by default.
# NOTE: stops and deletes all containers
make dev
```
