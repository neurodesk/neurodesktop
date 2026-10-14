#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ] || [[ "$1" == -* ]] || [ -z "$1" ]; then
    echo 'Usage: verify_slurm_image.sh IMAGE' >&2
    exit 2
fi

image_ref=$1
slurm_container="neurodesktop-slurm-test-$$"
cleanup() {
    local status=$?
    trap - EXIT
    if ! docker rm -f "$slurm_container" >/dev/null 2>&1; then
        echo 'Could not remove the Slurm test container.' >&2
        if [ "$status" -eq 0 ]; then status=1; fi
    fi
    exit "$status"
}
trap cleanup EXIT

docker run -d --init --user root --name "$slurm_container" \
    -e NEURODESKTOP_SLURM_ENABLE=1 -e NEURODESKTOP_SLURM_MODE=local \
    --entrypoint /bin/bash "$image_ref" -c 'sleep infinity'
docker exec "$slurm_container" /opt/neurodesktop/setup_and_start_slurm.sh
docker exec --user jovyan "$slurm_container" pytest /opt/tests/test_slurm.py -v
