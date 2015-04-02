FROM ubuntu:14.04

RUN apt-get update && apt-get upgrade -y && apt-get install python python-dev libcurl4-openssl-dev wget -y
RUN wget https://raw.github.com/pypa/pip/master/contrib/get-pip.py 
# because of http://stackoverflow.com/questions/27341064/
RUN python get-pip.py
RUN pip install tornado docker-py pycurl futures

ADD . /srv/tmpnb/
WORKDIR /srv/tmpnb/


ENV DOCKER_HOST unix://docker.sock

RUN pip install -r requirements.txt

CMD python orchestrate.py
