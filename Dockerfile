FROM ubuntu:14.04

RUN apt-get update && apt-get upgrade -y && apt-get install python3-pip python3 python3-dev libcurl4-openssl-dev -y
RUN pip3 install --upgrade pip

RUN pip install tornado docker-py pycurl futures

ADD . /srv/tmpnb/
WORKDIR /srv/tmpnb/

ENV DOCKER_HOST unix://docker.sock

RUN pip install -r requirements.txt

CMD python orchestrate.py
