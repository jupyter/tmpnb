FROM alpine:3.5

RUN apk update && apk add python3 py3-dateutil py3-curl py3-tornado py3-tz \
&& mkdir -p /srv/tmpnb && pip3 install docker-py \
&& ln -s /usr/bin/python3 /usr/bin/python

WORKDIR /srv/tmpnb/

COPY . /srv/tmpnb/

ENV DOCKER_HOST unix://docker.sock

CMD python orchestrate.py
