Goal: Reproducible setup for deploying tmpnb nodes scalably and safely

Ephemeral notebook environments can be deployed on bare metal, virtual, and
container orchestration engine deployments. Each come with their own costs,
benefits, risks and supportability.

### How do you dynamically add capacity for additional users elastically?

The way that tmpnb.org operates is by running the [tmpnb-redirector](https://github.com/jupyter/tmpnb-redirector), which provides
a means to do round-robin redirection to new nodes (at the network/DNS level).

```json
$ curl -s https://tmpnb.org/stats | jq
{
  "available": 107,
  "hosts": {
    "https://tmp39.tmpnb.org": {
      "available": 25,
      "container_image": "jupyter/demo:latest",
      "version": "0.1.0",
      "capacity": 64
    },
    "https://tmp41.tmpnb.org": {
      "available": 29,
      "container_image": "jupyter/demo:latest",
      "version": "0.1.0",
      "capacity": 64
    },
    "https://tmp40.tmpnb.org": {
      "available": 30,
      "container_image": "jupyter/demo:latest",
      "version": "0.1.0",
      "capacity": 64
    },
    "https://tmp35.tmpnb.org": {
      "available": 23,
      "container_image": "jupyter/demo:latest",
      "version": "0.1.0",
      "capacity": 64
    }
  },
  "version": "0.0.1",
  "capacity": 256
}
```

Health is checked continuously for each node within this setup and ensures that
websocket bottlenecks are constrained to each individual node e.g. `tmp41.tmpnb.org`.

This does require handling DNS yourself or setting records on deployment (the second is what is used for tmpnb.org).

With this approach, you can also divide up what's running on VMs, bare
metal, or container environments. You can run across multiple providers with this setup as well.

### How do you block outbound network access from notebook servers?

#### Daemon setting approach

The current implementation for nature and tmpnb.org is specified within an
Ansible playbook [in the jupyter/tmpnb-deploy](https://github.com/jupyter/tmpnb-deploy/blob/master/roles/notebook/files/docker) repository as an init file for Docker's daemon:

```bash
DOCKER_OPTS="--icc=false --ip-forward=false"
```

This requires configuring the proxy and tmpnb to use `--net=host` for networking. It's also not a feasible option within a swarm cluster.

#### Creating isolated networks

This predates the Docker networking plugins and overlay support within Carina though and Jupyter has been planning on supporting locked down networks by default. This requires upstream work in docker-py (finished) and work in tmpnb for handling networks (started).

[Configuring networking - tmpnb#187](https://github.com/jupyter/tmpnb/issues/187)

### How do you control disk usage, memory usage, and other quotas.

Memory usage and CPU usage are as granular as Docker itself allows, specified
through options on tmpnb deployment (e.g. `--cpu-shares` and `--mem-limit `).
Any time there's a new option that Docker allows us to pin down, we can expose it
directly for Docker.

Disk usage is an open problem for Docker [docker/docker#3804](https://github.com/docker/docker/issues/3804). However, since
the Docker images run underneath can have their own constraints for a subuser (Jupyter has a user named `jovyan`), there may be a way to do this in a typical Linux-centric way.

### How does authentication work with tmpnb

There is no authentication. JupyterHub is your pal there. It may be worth speccing out an auth system like how @parente's [mostly-tmpnb](https://github.com/parente/mostly-tmpnb) works.

