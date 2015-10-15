export TOKEN=$( head -c 30 /dev/urandom | xxd -p )
export POOL_SIZE=7
export OVERPROVISION_FACTOR=2
export CPU_SHARES=$(( (1024*${OVERPROVISION_FACTOR})/${POOL_SIZE} ))

docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN --restart=always \
            --name=proxy \
            -u root \
            jupyter/configurable-http-proxy \
              --default-target http://127.0.0.1:9999 \
              --port 80 \
              --api-ip 127.0.0.1 \
              --api-port 8001

docker run --rm --volumes-from swarm-data \
          busybox \
            sh -c "cp /etc/docker/server-cert.pem /etc/docker/cert.pem && cp /etc/docker/server-key.pem /etc/docker/key.pem"

docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=$TOKEN --restart=always \
           --volumes-from swarm-data \
           --name=tmpnb \
           -e DOCKER_HOST="tcp://127.0.0.1:42376" \
           -e DOCKER_TLS_VERIFY=1 \
           -e DOCKER_CERT_PATH=/etc/docker \
           jupyter/tmpnb python orchestrate.py --image='zischwartz/sparkdemo' \
           --command="/bin/bash -c 'IPYTHON_OPTS=\"notebook --NotebookApp.base_url=/{base_path} --ip=0.0.0.0\" pyspark --packages com.databricks:spark-csv_2.10:1.2.0'" \
           --pool_size=$POOL_SIZE \
           --mem_limit='512m' \
           --cpu_shares=$CPU_SHARES
