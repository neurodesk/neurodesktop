#!/bin/bash
set -euo pipefail

image=${1:?Usage: verify_kasm_image.sh IMAGE [--science]}
run_options=(-e CVMFS_DISABLE=true)
tests=(/opt/tests/test_kasm_workspace.py)
case "${2:-}" in
    '') ;;
    --science)
        run_options=(--privileged -e CVMFS_DISABLE=false -e NEURODESKTOP_TEST_KASM_SCIENCE=1)
        tests+=(/opt/tests/test_kasm_science.py)
        ;;
    *) echo 'Usage: verify_kasm_image.sh IMAGE [--science]' >&2; exit 2 ;;
esac
name="neurodesktop-kasm-check-$$"
volume="${name}-home"
password=$(openssl rand -hex 24)

cleanup() {
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

start_session() {
    docker run -d --name "$name" --shm-size=1g \
        -e VNC_PW="$password" "${run_options[@]}" \
        -e NEURODESKTOP_SLURM_ENABLE=false \
        -v "$volume:/home/kasm-user" "$image" >/dev/null
    for attempt in {1..90}; do
        if docker exec "$name" /opt/neurodesktop/kasm-healthcheck.sh 2>/dev/null; then
            if [ "${#tests[@]}" -eq 1 ] || docker exec "$name" \
                test -d /cvmfs/neurodesk.ardc.edu.au/neurodesk-modules; then
                return
            fi
        fi
        if [ "$(docker inspect -f '{{.State.Running}}' "$name")" != true ]; then
            break
        fi
        sleep 2
    done
    docker logs --tail 100 "$name" >&2
    return 1
}

docker volume create "$volume" >/dev/null
start_session
docker exec "$name" python -m pytest -q -s "${tests[@]}"
docker exec "$name" touch /home/kasm-user/kasm-persistence-check
docker stop -t 15 "$name" >/dev/null
docker rm "$name" >/dev/null
start_session
docker exec "$name" test -f /home/kasm-user/kasm-persistence-check
echo 'Kasm desktop, authentication, Jupyter, and home persistence checks passed.'
