#!/bin/bash
set -e

if [ "${1:-}" != "test" ]; then
    if docker ps --all | grep -w neurodesktop; then
        if docker ps --all | grep neurodeskapp; then
            echo "detected a Neurodeskapp container and ignoring it!"
        else
            bash stop_and_clean.sh
        fi
    fi
fi
# docker build -t neurodesktop:latest .
# docker run --shm-size=1gb -it --privileged --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -p 8080:8080 neurodesktop:latest
# -e CVMFS_DISABLE=true # will disable CVMFS for testing purposes
#
# Startup mode environment variables (default: lazy for faster Jupyter startup):
# -e NEURODESKTOP_CVMFS_STARTUP_MODE=lazy|eager  # lazy defers CVMFS mount to after Jupyter is ready
# -e NEURODESKTOP_SLURM_STARTUP_MODE=lazy|eager   # lazy defers Slurm startup to after Jupyter is ready
# Use eager to restore the original synchronous startup behavior.

docker build . -t neurodesktop:latest

if [ "${1:-}" = "test" ]; then
    echo "============================================================"
    echo "Running tests across multiple configurations (parallel)"
    echo "============================================================"

    # Define configurations: "cvmfs_disable:grant_sudo"
    CONFIGS=("false:no" "false:yes" "true:no" "true:yes")
    PIDS=()
    LOGDIR=$(mktemp -d)

    # Start all containers in parallel
    for i in "${!CONFIGS[@]}"; do
        IFS=':' read -r cvmfs_disable grant_sudo <<< "${CONFIGS[$i]}"
        name="neurodesktop-test-${i}"
        label="CVMFS_DISABLE=${cvmfs_disable}, GRANT_SUDO=${grant_sudo}"
        logfile="${LOGDIR}/${name}.log"

        docker rm -f "$name" 2>/dev/null || true
        docker volume create "${name}-home" 2>/dev/null || true

        echo "Starting: ${label} (${name})"
        docker run -d --shm-size=1gb --privileged --user=root \
            --name "$name" \
            --mount "source=${name}-home,target=/home/jovyan" \
            -v ~/neurodesktop-storage:/neurodesktop-storage \
            -e CVMFS_DISABLE="${cvmfs_disable}" \
            -e GRANT_SUDO="${grant_sudo}" \
            -e NEURODESKTOP_CVMFS_STARTUP_MODE=eager \
            -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
            neurodesktop:latest >/dev/null
    done

    # Wait for all containers to be ready, then run tests in parallel
    for i in "${!CONFIGS[@]}"; do
        IFS=':' read -r cvmfs_disable grant_sudo <<< "${CONFIGS[$i]}"
        name="neurodesktop-test-${i}"
        label="CVMFS_DISABLE=${cvmfs_disable}, GRANT_SUDO=${grant_sudo}"
        logfile="${LOGDIR}/${name}.log"

        (
            # Wait for Jupyter readiness
            for attempt in $(seq 1 60); do
                if docker exec "$name" curl -sf http://localhost:8888/api/status >/dev/null 2>&1; then
                    break
                fi
                sleep 2
            done

            echo "============================================================"
            echo "Config: ${label}"
            echo "============================================================"
            docker exec "$name" pytest /opt/tests/ -v
        ) > "$logfile" 2>&1 &
        PIDS+=($!)
    done

    echo "Waiting for all test runs to complete..."

    # Collect results
    FAILED=0
    for i in "${!CONFIGS[@]}"; do
        IFS=':' read -r cvmfs_disable grant_sudo <<< "${CONFIGS[$i]}"
        name="neurodesktop-test-${i}"
        label="CVMFS_DISABLE=${cvmfs_disable}, GRANT_SUDO=${grant_sudo}"
        logfile="${LOGDIR}/${name}.log"

        wait "${PIDS[$i]}" && status="PASSED" || { status="FAILED"; FAILED=1; }

        echo ""
        echo "============================================================"
        echo "${status}: ${label}"
        echo "============================================================"
        cat "$logfile"

        docker rm -f "$name" 2>/dev/null || true
        docker volume rm "${name}-home" 2>/dev/null || true
    done

    rm -rf "$LOGDIR"

    echo ""
    echo "============================================================"
    if [ $FAILED -eq 0 ]; then
        echo "ALL CONFIGURATIONS PASSED"
    else
        echo "SOME CONFIGURATIONS FAILED"
    fi
    echo "============================================================"
    exit $FAILED
fi



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

docker run --shm-size=1gb -it --privileged --user=root \
    --mount source=neurodesk-home,target=/home/jovyan \
    --name neurodesktop -v ~/neurodesktop-storage:/neurodesktop-storage \
    --add-host=host.docker.internal:host-gateway \
    -e OLLAMA_HOST="http://host.docker.internal:11434" \
    -e CVMFS_DISABLE=false \
    -e GRANT_SUDO=yes \
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
