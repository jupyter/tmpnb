# Configuration parameters
CULL_PERIOD ?= 30
CULL_TIMEOUT ?= 60
CULL_MAX ?= 120
LOGGING ?= debug
POOL_SIZE ?= 1
DOCKER_HOST ?= 127.0.0.1
DOCKER_NETWORK_NAME ?= tmpnb

tmpnb-image: Dockerfile
	docker build -t jupyter/tmpnb .

images: tmpnb-image demo-image minimal-image

minimal-image:
	docker pull jupyter/minimal-notebook

demo-image:
	docker pull jupyter/demo

proxy-image:
	docker pull jupyter/configurable-http-proxy

network:
	@docker network inspect $(DOCKER_NETWORK_NAME) >/dev/null 2>&1 || docker network create $(DOCKER_NETWORK_NAME)

proxy: proxy-image network
	docker run -d -e CONFIGPROXY_AUTH_TOKEN=devtoken \
		--network $(DOCKER_NETWORK_NAME) \
		-p 8000:8000 \
		-p 8001:8001 \
		--name proxy \
		jupyter/configurable-http-proxy \
		--default-target http://tmpnb:9999 --api-ip 0.0.0.0

tmpnb: minimal-image tmpnb-image network
	docker run -d -e CONFIGPROXY_AUTH_TOKEN=devtoken \
		-e CONFIGPROXY_ENDPOINT=http://proxy:8001 \
		--network $(DOCKER_NETWORK_NAME) \
		--name tmpnb \
		-v /var/run/docker.sock:/docker.sock jupyter/tmpnb python orchestrate.py \
		--image=jupyter/minimal-notebook --cull_timeout=$(CULL_TIMEOUT) --cull_period=$(CULL_PERIOD) \
		--logging=$(LOGGING) --pool_size=$(POOL_SIZE) --cull_max=$(CULL_MAX) \
		--docker_network=$(DOCKER_NETWORK_NAME)

dev: cleanup network proxy tmpnb open

open:
	docker ps | grep tmpnb
	-open http:`echo $(DOCKER_HOST) | cut -d":" -f2`:8000

cleanup:
	-docker stop `docker ps -aq --filter name=tmpnb --filter name=proxy --filter name=minimal-notebook`
	-docker rm   `docker ps -aq --filter name=tmpnb --filter name=proxy --filter name=minimal-notebook`
	-docker images -q --filter "dangling=true" | xargs docker rmi

log-tmpnb:
	docker logs -f tmpnb

log-proxy:
	docker logs -f proxy

.PHONY: cleanup
