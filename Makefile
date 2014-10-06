images: Dockerfile image/Dockerfile
	docker build -t jupyter/tmpnb .
	docker build -t jupyter/demo image

dev: cleanup-images images
	docker pull jupyter/configurable-http-proxy
	docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=devtoken -v /var/run/docker.sock:/docker.sock jupyter/tmpnb
	docker run --net=host -d -e CONFIGPROXY_AUTH_TOKEN=devtoken jupyter/configurable-http-proxy --default-target http://127.0.0.1:9999

cleanup-images:
	-docker kill $( docker ps -aq )
	-docker rm $( docker ps -aq )
	-docker images -q --filter "dangling=true" | xargs docker rmi

.PHONY: cleanup-images
