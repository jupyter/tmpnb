tmpnb: Dockerfile
	docker build -t jupyter/tmpnb .

images: tmpnb demo-image minimal-image

minimal-image:
	docker build -t jupyter/minimal images/minimal

demo-image:
	docker build -t jupyter/demo images/demo

dev: cleanup-images minimal-image tmpnb
	docker pull jupyter/configurable-http-proxy
	docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=devtoken -v /var/run/docker.sock:/docker.sock jupyter/tmpnb
	docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=devtoken jupyter/configurable-http-proxy --default-target http://127.0.0.1:9999 --image=jupyter/minimal

cleanup-images:
	-docker stop `docker ps -aq`
	-docker rm   `docker ps -aq`
	-docker images -q --filter "dangling=true" | xargs docker rmi

.PHONY: cleanup-images
