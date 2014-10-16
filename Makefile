# Configuration parameters
CULL_TIMEOUT ?= 60
LOGGING ?= debug
POOL_SIZE ?= 3

tmpnb-image: Dockerfile
	docker build -t jupyter/tmpnb .

images: tmpnb-image demo-image minimal-image

minimal-image:
	docker build -t jupyter/minimal images/minimal

demo-image:
	docker build -t jupyter/demo images/demo

proxy-image:
	docker pull jupyter/configurable-http-proxy

proxy: proxy-image
	docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=devtoken jupyter/configurable-http-proxy --default-target http://127.0.0.1:9999

tmpnb: minimal-image tmpnb-image
	docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=devtoken \
		-v /var/run/docker.sock:/docker.sock jupyter/tmpnb python orchestrate.py \
		--image=jupyter/minimal --cull_timeout=$(CULL_TIMEOUT) --logging=$(LOGGING) \
		--pool_size=$(POOL_SIZE)

dev: cleanup proxy tmpnb

cleanup:
	-docker stop `docker ps -aq`
	-docker rm   `docker ps -aq`
	-docker images -q --filter "dangling=true" | xargs docker rmi

.PHONY: cleanup-images
