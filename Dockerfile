FROM alpine:3.5

RUN apk update && apk add python3 py3-curl \
&& mkdir -p /srv/tmpnb && pip3 install docker-py tornado  pytz \
&& pip3 install --upgrade pip && rm -fr /root/.cache/pip \
&& ln -s /usr/bin/python3 /usr/bin/python

WORKDIR /srv/tmpnb/

COPY . /srv/tmpnb/

ENV DOCKER_HOST unix://docker.sock

CMD python orchestrate.py
