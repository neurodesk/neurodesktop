docker stop neurodesktop
docker rm neurodesktop
docker build -t neurodesktop:latest .
docker run --shm-size=1gb -it --privileged --name neurodesktop -v C:/neurodesktop-storage:/neurodesktop-storage -e OLLAMA_HOST=http://host.docker.internal:11434 -p 127.0.0.1:8888:8888 --user=root neurodesktop:latest
@REM docker run --shm-size=1gb -it --privileged --name neurodesktop -v C:/neurodesktop-storage:/neurodesktop-storage -e USER=user -e CVMFS_DISABLE=true -p 8080:8080 neurodesktop:latest
