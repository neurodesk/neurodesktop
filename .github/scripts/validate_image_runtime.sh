#!/usr/bin/env bash
set -e

usage() {
  echo 'Usage: validate_image_runtime.sh IMAGE regular CVMFS_DISABLE GRANT_SUDO NEEDS_CVMFS | IMAGE hpc' >&2
  exit 2
}

[ "$#" -ge 2 ] || usage
image_ref=$1
mode=$2
case "$image_ref" in ''|-*) usage ;; esac
case "$mode" in
  regular)
    [ "$#" -eq 5 ] || usage
    cvmfs_disable=$3
    grant_sudo=$4
    needs_cvmfs=$5
    case "$cvmfs_disable" in true|false) ;; *) usage ;; esac
    case "$grant_sudo" in no|yes|packages) ;; *) usage ;; esac
    case "$needs_cvmfs" in true|false) ;; *) usage ;; esac
    ;;
  hpc) [ "$#" -eq 2 ] || usage ;;
  *) usage ;;
esac

hpc_home_dir=''
hpc_passwd_file=''
hpc_group_file=''

cleanup() {
  local image_test_status=$?
  trap - EXIT
  if ! docker rm -f neurodesktop-test >/dev/null 2>&1; then
    echo '::warning::Could not remove the image test container.' >&2
    if [ "$image_test_status" -eq 0 ]; then image_test_status=1; fi
  fi
  if [ -n "${hpc_home_dir:-}" ]; then
    if ! docker run --rm --user 0:0 --entrypoint chown \
      -v "$hpc_home_dir":/cleanup "$image_ref" \
      -R "$(id -u):$(id -g)" /cleanup >/dev/null 2>&1; then
      echo '::warning::Could not restore ownership of the HPC test home.' >&2
      if [ "$image_test_status" -eq 0 ]; then image_test_status=1; fi
    fi
    if ! rm -rf -- "$hpc_home_dir" "${hpc_passwd_file:-}" "${hpc_group_file:-}"; then
      echo '::warning::Could not remove HPC test files.' >&2
      if [ "$image_test_status" -eq 0 ]; then image_test_status=1; fi
    fi
  fi
  exit "$image_test_status"
}
trap cleanup EXIT

if [ "$mode" = regular ]; then
  docker_args=()
  if [ "$needs_cvmfs" = "true" ]; then
    docker_args=(-v /cvmfs:/cvmfs:shared)
  fi
  docker rm -f neurodesktop-test 2>/dev/null || true
  docker run -d --shm-size=1gb --privileged --user=root \
    --name neurodesktop-test \
    "${docker_args[@]}" \
    -e "CVMFS_DISABLE=$cvmfs_disable" \
    -e "GRANT_SUDO=$grant_sudo" \
    -e NEURODESKTOP_CVMFS_STARTUP_MODE=eager \
    -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
    "$image_ref"
  echo "Waiting for container startup..."
  for i in $(seq 1 60); do
    code=$(docker exec neurodesktop-test curl -so /dev/null -w '%{http_code}' \
             --max-time 2 http://localhost:8888/api/status 2>/dev/null || echo 000)
    if echo "$code" | grep -Eq '^[0-9]{3}$' && [ "$code" != "000" ]; then
      echo "Container ready after ~$((i*2))s (HTTP ${code})"
      break
    fi
    sleep 2
  done
  if [ "$grant_sudo" = "packages" ]; then
    docker exec -u root neurodesktop-test pytest /opt/tests/test_security_policy.py -v
  fi
  docker exec -e NEURODESKTOP_TEST_ALLOW_GLOBAL_DESKTOP_SERVICES=1 \
    -u jovyan neurodesktop-test pytest /opt/tests/ -v
else
  hpc_user=sciget
  hpc_uid=5000
  hpc_gid=5000
  hpc_home_dir="$(mktemp -d)"
  hpc_passwd_file="$(mktemp)"
  hpc_group_file="$(mktemp)"

  cat > "$hpc_passwd_file" <<EOF
root:x:0:0:root:/root:/bin/bash
jovyan:x:1000:100:jovyan:/home/jovyan:/bin/bash
${hpc_user}:x:${hpc_uid}:${hpc_gid}:${hpc_user} (HPC simulated):/home/jovyan:/bin/bash
nobody:x:65534:65534:nobody:/:/usr/sbin/nologin
EOF
  cat > "$hpc_group_file" <<EOF
root:x:0:
users:x:100:jovyan,${hpc_user}
${hpc_user}:x:${hpc_gid}:
nogroup:x:65534:
EOF
  chmod 0777 "$hpc_home_dir"
  chmod 0644 "$hpc_passwd_file" "$hpc_group_file"

  docker rm -f neurodesktop-test 2>/dev/null || true
  docker run -d --shm-size=1gb \
    --user "${hpc_uid}:${hpc_gid}" \
    --name neurodesktop-test \
    -v "${hpc_home_dir}:/home/jovyan" \
    -v "${hpc_passwd_file}:/etc/passwd:ro" \
    -v "${hpc_group_file}:/etc/group:ro" \
    -e CVMFS_DISABLE=true \
    -e "NB_USER=${hpc_user}" \
    -e "NB_UID=${hpc_uid}" \
    -e "NB_GID=${hpc_gid}" \
    -e HOME=/home/jovyan \
    -e "USER=${hpc_user}" \
    -e "LOGNAME=${hpc_user}" \
    -e APPTAINER_CONTAINER=1 \
    -e APPTAINER_NAME=neurodesktop-test-hpc \
    -e NEURODESKTOP_CVMFS_STARTUP_MODE=lazy \
    -e NEURODESKTOP_SLURM_ENABLE=0 \
    -e NEURODESKTOP_SLURM_STARTUP_MODE=eager \
    "$image_ref"
  echo "Waiting for container startup..."
  ready=0
  for i in $(seq 1 90); do
    if ! docker inspect -f '{{.State.Running}}' neurodesktop-test 2>/dev/null | grep -q true; then
      echo "::error::Container exited during startup (HPC mode)."
      docker logs --tail 120 neurodesktop-test || true
      exit 1
    fi
    code=$(docker exec neurodesktop-test curl -so /dev/null -w '%{http_code}' \
             --max-time 2 http://localhost:8888/api/status 2>/dev/null || echo 000)
    if echo "$code" | grep -Eq '^[0-9]{3}$' && [ "$code" != "000" ]; then
      echo "Container ready after ~$((i*2))s (HTTP ${code})"
      ready=1
      break
    fi
    sleep 2
  done
  if [ "$ready" -ne 1 ]; then
    echo "::error::Container did not reach /api/status in 180s (HPC mode)."
    docker logs --tail 120 neurodesktop-test || true
    exit 1
  fi
  docker exec -e NEURODESKTOP_TEST_ALLOW_GLOBAL_DESKTOP_SERVICES=1 \
    neurodesktop-test pytest /opt/tests/ -v
fi
