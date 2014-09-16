# Designed to be run as 
# 
# docker run -it -p 9999:8888 ipython/latest

FROM ipython/scipystack

MAINTAINER IPython Project <ipython-dev@scipy.org>

# Can't directly add the source as it won't have the submodules
RUN mkdir -p /srv/
WORKDIR /srv/
RUN git clone --recursive https://github.com/ipython/ipython.git
WORKDIR /srv/ipython/

# Installing certain dependencies directly
RUN pip2 install fabric
RUN pip3 install jsonschema jsonpointer fabric

RUN python setup.py submodule

# .[all] only works with -e
# Can't use -e because ipython2 and ipython3 will clobber each other
RUN pip2 install .
RUN pip3 install .

EXPOSE 8888

ADD notebook.sh /usr/local/bin/notebook.sh 
RUN chmod a+x /usr/local/bin/notebook.sh

# jupyter is our user
RUN useradd -m -s /bin/bash jupyter

USER jupyter
ENV HOME /home/jupyter
ENV SHELL /bin/bash
ENV USER jupyter

WORKDIR /home/jupyter/

ENTRYPOINT ["/usr/local/bin/notebook.sh"]
