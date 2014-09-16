#!/usr/bin/env bash
myrand=`head -c 30 /dev/urandom | xxd -p`
cont=$( docker run -it -d -P -e RAND_BASE=$myrand tmpnb )
port=$( docker port $cont 8888 | cut -d":" -f2 )

echo $myrand
echo $cont
echo $port

curl -H "Authorization: token $CONFIGPROXY_AUTH_TOKEN"  -XPOST -d '{"target":"http://localhost:'$port'"}' http://localhost:8001/api/routes/$myrand
curl -H "Authorization: token $CONFIGPROXY_AUTH_TOKEN"  http://localhost:8001/api/routes | python -m json.tool
