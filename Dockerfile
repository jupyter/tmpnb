FROM ubuntu:14.04

RUN apt-get update && apt-get upgrade -y && apt-get install python-pip python python-dev libcurl4-openssl-dev -y
RUN pip install --upgrade pip

RUN mkdir -p /srv/tmpnb
WORKDIR /srv/tmpnb/
ADD requirements.txt /srv/tmpnb
RUN pip install -r requirements.txt

ADD . /srv/tmpnb/

ENV DOCKER_HOST unix://docker.sock

CMD python orchestrate.py
