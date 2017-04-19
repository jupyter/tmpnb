FROM alpine:3.5

RUN apk update && apk add python3 py3-curl py3-tz py3-tornado \
    && pip3 install docker-py==1.7.2 \
    && pip3 install --upgrade pip \
    && rm -fr /root/.cache/pip && rm /var/cache/apk/* \
    && ln -s /usr/bin/python3 /usr/bin/python \
    && mkdir -p /srv/tmpnb 

WORKDIR /srv/tmpnb/

COPY . /srv/tmpnb/

ENV DOCKER_HOST unix://docker.sock

CMD python orchestrate.py
