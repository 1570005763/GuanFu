sudo docker build -t dettrace:guanfu .
sudo docker run -it -v $(pwd)/mount:/home/rebuild/mount  --network host  --rm --privileged dettrace:guanfu  /bin/bash   /home/rebuild/start.sh