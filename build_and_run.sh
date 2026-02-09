#!/bin/bash
set -e

if docker ps --all | grep neurodesktop; then
    if docker ps --all | grep neurodeskapp; then
        echo "detected a Neurodeskapp container and ignoring it!"
    else
        bash stop_and_clean.sh
    fi
fi
# docker build -t neurodesktop:latest .
# docker run --shm-size=1gb -it --privileged --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -p 8080:8080 neurodesktop:latest
# -e CVMFS_DISABLE=true # will disable CVMFS for testing purposes

docker build . -t neurodesktop:latest



# podman build . -t neurodesktop:latest

# Test with internal CVMFS
# docker run --shm-size=1gb -it --cap-add SYS_ADMIN --security-opt apparmor:unconfined \
#     --device=/dev/fuse --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage \
#     -p 8888:8888 \
#     --user=root -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
#     neurodesktop:latest

# Test with persistent home directory
# docker volume create neurodesk-home
# docker run --shm-size=1gb -it --privileged --user=root \
#     --device=/dev/fuse --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage \
#     --mount source=neurodesk-home,target=/home/jovyan \
#     -p 8888:8888 \
#     -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
#     neurodesktop:latest

# Test Offline mode with CVMFS disabled
# docker volume create neurodesk-home
# docker run --shm-size=1gb -it --privileged --user=root \
#     --device=/dev/fuse --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage \
#     --mount source=neurodesk-home,target=/home/jovyan \
#     -e CVMFS_DISABLE=true \
#     -p 8888:8888 \
#     -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
#     neurodesktop:latest

# # Test Offline mode with CVMFS disabled without --device=/dev/fuse
# docker volume create neurodesk-home
# docker run --shm-size=1gb -it --privileged --user=root \
#     --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage \
#     --mount source=neurodesk-home,target=/home/jovyan \
#     -e CVMFS_DISABLE=true \
#     -p 8888:8888 \
#     -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
#     neurodesktop:latest





# Test Online mode with CVMFS enabled without --device=/dev/fuse
docker volume create neurodesk-home

# Mount local test webapp containers if they exist
TEST_WEBAPP_MOUNT=""
NEUROCONTAINERS_SIFS_DIR="../neurocontainers/sifs"
if [ -d "$NEUROCONTAINERS_SIFS_DIR" ]; then
    for sif_file in "$NEUROCONTAINERS_SIFS_DIR"/*.sif; do
        if [ -f "$sif_file" ]; then
            # Extract app name from filename (e.g., rstudio_2023.12.1.sif -> rstudio)
            filename=$(basename "$sif_file")
            app_name="${filename%%_*}"
            echo "Mounting local test container: $app_name"
            TEST_WEBAPP_MOUNT="$TEST_WEBAPP_MOUNT -v $(realpath "$sif_file"):/opt/neurodesktop-test-webapps/$app_name/$app_name.sif:ro"
        fi
    done
fi

    # --mount source=neurodesk-home,target=/home/jovyan \
docker run --shm-size=1gb -it --privileged --user=root \
    --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage \
    --add-host=host.docker.internal:host-gateway \
    -e CVMFS_DISABLE=false \
    -e OLLAMA_HOST="http://host.docker.internal:11434" \
    -p 127.0.0.1:8888:8888 \
    --cpus=10 --memory=32g \
    -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
    $TEST_WEBAPP_MOUNT \
    neurodesktop:latest


# podman volume create neurodesk-home &&
# sudo podman run \
#   --shm-size=1gb -it --privileged --user=root --name neurodesktop \
#   -v ~/neurodesktop-storage:/neurodesktop-storage \
#   --mount type=volume,source=neurodesk-home,target=/home/jovyan \
#   -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
#   -p 8888:8888 \
#   -e NEURODESKTOP_VERSION=development neurodesktop:latest


# Test normal mode without --device=/dev/fuse
# docker volume create neurodesk-home
# docker run --shm-size=1gb -it --privileged --user=root \
#     --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage \
#     --mount source=neurodesk-home,target=/home/jovyan \
#     -p 8888:8888 \
#     -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
#     neurodesktop:latest

# Run with external CVMFS:
# docker run --shm-size=1gb -it --cap-add SYS_ADMIN --security-opt apparmor:unconfined \
#     --device=/dev/fuse --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage \
#     -v /cvmfs:/cvmfs -p 8888:8888 \
#     --user=root -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
#     neurodesktop:latest

# launch with custom token
# docker run --shm-size=1gb -it --cap-add SYS_ADMIN --security-opt apparmor:unconfined \
#     --device=/dev/fuse --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage \
#     --mount source=neurodesk-home,target=/home/jovyan \
#     -p 8888:8888 \
#     --user=root -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
#     neurodesktop:latest start.sh jupyter lab --ServerApp.password="" --no-browser --expose-app-in-browser --ServerApp.token="jlab:srvr:123" --ServerApp.port=33163 --LabApp.quit_button=False
