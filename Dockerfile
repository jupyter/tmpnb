FROM alpine

RUN REPO=http://dl-cdn.alpinelinux.org/alpine \
&& echo -e "$REPO/v3.5/main\n\
$REPO/v3.5/community\n\
$REPO/edge/main\n\
$REPO/edge/community\n\
$REPO/edge/testing" > /etc/apk/repositories \
&& apk update && apk add python3-dev py3-dateutil curl-dev openssl-dev gcc g++ \
&& mkdir -p /srv/tmpnb \
&& echo -e "tornado==4.4.3\n\
docker-py==1.10.6\n\
pycurl==7.43.0\n\
pytz==2017.2" > /srv/tmpnb/requirements.txt \
&& pip3 install -r /srv/tmpnb/requirements.txt \
&& apk del --purge python3-dev curl-dev openssl-dev zlib-dev gcc g++ \
&& apk add libcurl libcrypto1.0 

WORKDIR /srv/tmpnb/

COPY . /srv/tmpnb/

ENV DOCKER_HOST unix://docker.sock

CMD python3 orchestrate.py
