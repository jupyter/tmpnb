images: Dockerfile image/Dockerfile
	docker build -t jupyter/tmpnb .
	docker build -t jupyter/demo image

cleanup-images:
	docker kill $( docker ps -aq )
	docker rm $( docker ps -aq )
	docker images -q --filter "dangling=true" | xargs docker rmi

.PHONY: cleanup-images
