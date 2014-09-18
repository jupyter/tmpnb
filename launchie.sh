#!/usr/bin/env bash
export CONFIGPROXY_AUTH_TOKEN=`head -c 30 /dev/urandom | xxd -p`
configurable-http-proxy --default-target=http://localhost:9999 &
python rando.py $@
kill %%
