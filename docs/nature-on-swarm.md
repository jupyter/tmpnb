## Nature on Swarm

tmpnb connects to swarm in the same way it connects to the `DOCKER_API`.

example setup for working with tmpnb directly on swarm.

```bash
export TOKEN=$( head -c 30 /dev/urandom | xxd -p )
export POOL_SIZE=7
export OVERPROVISION_FACTOR=2
export CPU_SHARES=$(( (1024*${OVERPROVISION_FACTOR})/${POOL_SIZE} ))

docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN --restart=always \
            -e constraint:node==8d21b232-9927-4315-b3e6-ba7a102ecfc8-n1 \
            --name=proxy \
            --volumes-from tmpnb-certs \
            -u root \
            jupyter/configurable-http-proxy \
              --default-target http://127.0.0.1:9999 \
              --ssl-key /etc/ssl/tmpnb.org.key \
              --ssl-cert /etc/ssl/tmpnb.org.crt \
              --port 443 \
              --redirect-port 80 \
              --api-ip 127.0.0.1 \
              --api-port 8001

docker run --rm --volumes-from swarm-data \
          -e constraint:node==8d21b232-9927-4315-b3e6-ba7a102ecfc8-n1 \
          busybox \
            sh -c "cp /etc/docker/server-cert.pem /etc/docker/cert.pem && cp /etc/docker/server-key.pem /etc/docker/key.pem"

docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN --restart=always \
           -e constraint:node==8d21b232-9927-4315-b3e6-ba7a102ecfc8-n1 \
           --volumes-from swarm-data \
           --name=tmpnb \
           -e DOCKER_HOST="tcp://127.0.0.1:42376" \
           -e DOCKER_TLS_VERIFY=1 \
           -e DOCKER_CERT_PATH=/etc/docker \
           jupyter/tmpnb python orchestrate.py --image='jupyter/nature-demo' \
           --redirect_uri='/notebooks/Nature.ipynb' \
           --command="ipython3 notebook --NotebookApp.base_url={base_path} --ip=0.0.0.0 --port {port}" \
           --pool_size=$POOL_SIZE \
           --mem_limit='512m' \
           --cpu_shares=$CPU_SHARES
```


docker pull jupyter/minimal
export TOKEN=$( head -c 30 /dev/urandom | xxd -p )
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN --name=proxy jupyter/configurable-http-proxy --default-target http://127.0.0.1:9999
docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN --name=tmpnb -v /var/run/docker.sock:/docker.sock jupyter/tmpnb
