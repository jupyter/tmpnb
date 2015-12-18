## tmpnb, the temporary notebook service

[![Gitter](https://badges.gitter.im/Join Chat.svg)](https://gitter.im/jupyter/tmpnb?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge)

Launches "temporary" Jupyter notebook servers.

![tmpnb architecture](https://cloud.githubusercontent.com/assets/836375/5911140/c53e3978-a587-11e4-86a5-695469ef23a5.png)

tmpnb launches a docker container for each user that requests one. In practice, this gets used to [provide temporary notebooks](https://tmpnb.org), demo the IPython notebook as part of [a Nature article](http://www.nature.com/news/interactive-notebooks-sharing-the-code-1.16261), or even [provide Jupyter kernels for publications](http://odewahn.github.io/publishing-workflows-for-jupyter/#1).

People have used it at user groups, meetups, and workshops to provide temporary access to a full system without any installation whatsoever.

#### Quick start

Get Docker, then:

```
make user
```

BAM! Visit your host on port 8000 and you have a working tmpnb setup. If you examine the user target in the Makefile, you will notice the ` -v /var/run/docker.sock:/docker.sock` configuration which causes the orchestrator container to mount the docker client, which allows the orchestrator container to spawn docker containers on the host (see [this article](http://nathanleclaire.com/blog/2014/07/12/10-docker-tips-and-tricks-that-will-make-you-sing-a-whale-song-of-joy/#bind-mount-the-docker-socket-on-docker-run:1765430f0793020845eca6c8326a4e45) for more information).  Note, if you are using boot2docker, then you can find your docker host's ip address by running the following command in your console:

```
boot2docker ip
```

If it didn't come up, try running `docker ps -a` and `docker logs tmpnb` to help diagnose issues.

#### Advanced configuration

If you need to set the `docker-version` or other options, they can be passed to `jupyter/tmpnb` directly:

```
make cleanup
make proxy
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN -v /var/run/docker.sock:/docker.sock jupyter/tmpnb python orchestrate.py --cull-timeout=60 --docker-version="1.13" --command="jupyter notebook --NotebookApp.base_url={base_path} --ip=0.0.0.0 --port {port}"
```

Note that if you do not pass a value to `docker-version`, tmpnb will automatically use the Docker API version provided by the server.

#### Launching with *your own* Docker images

tmpnb can run any Docker container provided by the `--image` option, so long as the `--command` option tells where the `{base_path}` and `{port}`. Those are literal strings, complete with curly braces that tmpnb will replace with an assigned `base_path` and `port`.

```
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN \
           -v /var/run/docker.sock:/docker.sock \
           jupyter/tmpnb python orchestrate.py --image='jupyter/demo' --command="jupyter notebook --NotebookApp.base_url={base_path} --ip=0.0.0.0 --port {port}"
```

#### Using [jupyter/docker-stacks](https://github.com/jupyter/docker-stacks) images

When using [jupyter/docker-stacks](https://github.com/jupyter/docker-stacks) images with tmpnb, care has to be taken with the command used to launch the notebook images.  Docker-stacks uses a special shell script to launch the server, which handles launching it as the correct user.  You need to use that script instead of `ipython notebook` or `jupyter notebook` in the `--command` argument.  Here's an example of how this is done to launch the `minimal-notebook` image:

```
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN \
           -v /var/run/docker.sock:/docker.sock \
           jupyter/tmpnb python orchestrate.py --image="docker-stacks/minimal-notebook" \
           --command="start-notebook.sh \"--NotebookApp.base_url={base_path} --NotebookApp.allow_origin='*' --port={port}\""
```

#### Options

```
Usage: orchestrate.py [OPTIONS]

Options:

  --allow_credentials              Sets the Access-Control-Allow-Credentials
                                   header.
  --allow_headers                  Sets the Access-Control-Allow-Headers
                                   header.
  --allow_methods                  Sets the Access-Control-Allow-Methods
                                   header.
  --allow_origin                   Set the Access-Control-Allow-Origin header.
                                   Use '*' to allow any origin to access.
  --assert_hostname                Verify hostname of Docker daemon. (default
                                   False)
  --command                        Command to run when booting the image. A
                                   placeholder for  {base_path} should be
                                   provided. A placeholder for {port} and {ip}
                                   can be  provided. (default jupyter notebook
                                   --no-browser --port {port} --ip=0.0.0.0
                                   --NotebookApp.base_url=/{base_path})
  --container_ip                   Host IP address for containers to bind to.
                                   If host_network=True, the IP
                                   address for notebook servers to bind to.
                                   (default 127.0.0.1)
  --container_port                 Within container port for notebook servers
                                   to bind to.  If host_network=True, the
                                   starting port assigned to notebook servers
                                   on the host  network. (default 8888)
  --cpu_shares                     Limit CPU shares, per container
  --cull_period                    Interval (s) for culling idle containers.
                                   (default 600)
  --cull_timeout                   Timeout (s) for culling idle containers.
                                   (default 3600)
  --docker_version                 Version of the Docker API to use (default
                                   auto)
  --expose_headers                 Sets the Access-Control-Expose-Headers
                                   header.
  --help                           show this help information
  --host_network                   Attaches the containers to the host
                                   networking instead of the  default docker
                                   bridge. Affects the semantics of
                                   container_port and container_ip. (default
                                   False)
  --image                          Docker container to spawn for new users.
                                   Must be on the system already (default
                                   jupyter/minimal-notebook)
  --ip                             ip for the main server to listen on
                                   [default: all interfaces]
  --max_age                        Sets the Access-Control-Max-Age header.
  --max_dock_workers               Maximum number of docker workers (default 2)
  --mem_limit                      Limit on Memory, per container (default
                                   512m)
  --pool_name                      Container name fragment used to identity
                                   containers that belong to this instance.
  --pool_size                      Capacity for containers on this system. Will
                                   be prelaunched at startup. (default 10)
  --port                           port for the main server to listen on
                                   (default 9999)
  --redirect_uri                   URI to redirect users to upon initial
                                   notebook launch (default /tree)
  --static_files                   Static files to extract from the initial
                                   container launch
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
