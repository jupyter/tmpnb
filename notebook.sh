#!/bin/bash

set -euo pipefail
IFS=$'\n\t'

ipython2 kernelspec install-self
ipython3 kernelspec install-self
ipython3 notebook --no-browser --port 8888 --ip=0.0.0.0 --NotebookApp.base_url=/$RAND_BASE
