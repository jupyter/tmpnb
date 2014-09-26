# Designed to be run as 
# 
# docker run -it -p 9999:8888 ipython/latest

FROM ipython/scipystack

MAINTAINER IPython Project <ipython-dev@scipy.org>

# Can't directly add the source as it won't have the submodules
RUN mkdir -p /srv/
WORKDIR /srv/
RUN git clone --recursive --depth 1 https://github.com/ipython/ipython.git
WORKDIR /srv/ipython/
RUN chmod a+rw /srv/ipython/examples

# Add Google Analytics templates
ADD ga/ /srv/ga/

# Installing certain dependencies directly
RUN pip2 install fabric
RUN pip3 install jsonschema jsonpointer fabric

RUN python setup.py submodule

# .[all] only works with -e
# Can't use -e because ipython2 and ipython3 will clobber each other
RUN pip2 install .
RUN pip3 install .

# Dependencies for the example notebooks
RUN apt-get build-dep -y mpi4py
RUN pip install scikit-image vincent dill networkx mpi4py

# Install R and the R kernel
RUN apt-get install -y r-base r-base-dev r-cran-rcurl
ADD R/ /srv/R/

# Julia Installation
RUN apt-get install software-properties-common python-software-properties -y
RUN add-apt-repository ppa:staticfloat/juliareleases
RUN add-apt-repository ppa:staticfloat/julia-deps
RUN apt-get update
RUN apt-get install julia -y
RUN apt-get install libnettle4

ADD Julia/ /srv/Julia/

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

# IPython introductions
ADD Welcome.ipynb /home/jupyter/
RUN cp -r /srv/ipython/examples /home/jupyter/examples

# R installation
# Copying files instead of adding due to current permissions issue with ADD
RUN cp /srv/R/Renviron /home/jupyter/.Renviron
RUN cp /srv/R/Rprofile /home/jupyter/.Rprofile
RUN mkdir /home/jupyter/.R/

RUN cat /srv/R/install_kernel.R | R --no-save
RUN cat /srv/R/install_pkgs.R   | R --no-save

# IJulia installation
RUN julia -e 'Pkg.add("IJulia")'
# Julia packages
RUN julia -e 'Pkg.add("Gadfly")'
# Install kernel directly (for now)
RUN mkdir -p /home/jupyter/.ipython/kernels/julia/
RUN cp /srv/Julia/kernel.json /home/jupyter/.ipython/kernels/julia/kernel.json

CMD ["/usr/local/bin/notebook.sh"]
