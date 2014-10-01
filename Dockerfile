FROM ubuntu:14.04

RUN apt-get update && apt-get upgrade -y && apt-get install python-pip python python-dev libcurl4-openssl-dev -y
RUN pip install tornado docker-py

ADD . /srv/tmpnb/
WORKDIR /srv/tmpnb/

ENV DOCKER_HOST unix://docker.sock

RUN pip install -r requirements.txt
