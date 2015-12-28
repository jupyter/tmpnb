FROM ubuntu:14.04

RUN apt-get update && apt-get upgrade -y && apt-get install python-pip python python-dev libcurl4-openssl-dev -y
RUN pip install --upgrade pip

RUN mkdir -p /srv/tmpnb
WORKDIR /srv/tmpnb/

# Copy the requirements.txt in by itself first to avoid docker cache busting
# any time any file in the project changes
COPY requirements.txt /srv/tmpnb/requirements.txt
RUN pip install -r requirements.txt

# Now copy in everything else
COPY . /srv/tmpnb/

ENV DOCKER_HOST unix://docker.sock

CMD python orchestrate.py
