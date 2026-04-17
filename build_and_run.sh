#!/bin/bash
set -e

if [ "${1:-}" != "test" ] && [ "${1:-}" != "fulltest" ] && [ "${1:-}" != "fulltest_verbose" ] && [ "${1:-}" != "hpctest" ] && [ "${1:-}" != "hpc" ]; then
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

run_single_test() {
    # Run a single test configuration with live output
    local cvmfs_disable="$1"
    local grant_sudo="$2"
    local name="neurodesktop-test"

    echo "============================================================"
    echo "Testing: CVMFS_DISABLE=${cvmfs_disable}, GRANT_SUDO=${grant_sudo}"
    echo "============================================================"

    docker rm -f "$name" 2>/dev/null || true
    docker volume create "${name}-home" 2>/dev/null || true

    docker run -d --shm-size=1gb --privileged --user=root \
        --name "$name" \
        --mount "source=${name}-home,target=/home/jovyan" \
        -v ~/neurodesktop-storage:/neurodesktop-storage \
        -e CVMFS_DISABLE="${cvmfs_disable}" \
        -e GRANT_SUDO="${grant_sudo}" \
        -e NEURODESKTOP_CVMFS_STARTUP_MODE=eager \
        -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
        neurodesktop:latest >/dev/null

    echo "Waiting for container startup..."
    for i in $(seq 1 60); do
        # Accept any HTTP status - newer jupyter-server gates /api/status
        # behind the auth token, so `curl -sf` (2xx-only) hangs on 403.
        # Only accept a clean three-digit non-000 status so a refused
        # connection (which prints "000" AND exits non-zero) does not get
        # concatenated with the "000" fallback to form a false-ready "000000".
        code=$(docker exec "$name" curl -so /dev/null -w '%{http_code}' \
                 --max-time 2 http://localhost:8888/api/status 2>/dev/null || echo 000)
        if echo "$code" | grep -Eq '^[0-9]{3}$' && [ "$code" != "000" ]; then
            echo "Container ready after ~$((i*2))s (HTTP ${code})"
            break
        fi
        sleep 2
    done

    docker exec -u jovyan "$name" pytest /opt/tests/ -v
    local result=$?

    docker rm -f "$name" 2>/dev/null || true
    docker volume rm "${name}-home" 2>/dev/null || true
    return $result
}

if [ "${1:-}" = "test" ]; then
    # Quick test: default user configuration (CVMFS enabled, sudo granted)
    run_single_test false yes
    exit $?
fi

# ---------------------------------------------------------------------------
# HPC simulation helpers (shared by `hpc`, `hpctest`, and the `fulltest` HPC
# configuration). Mirrors Apptainer on Sherlock:
#   - no root / --privileged / sudo
#   - container user is NOT jovyan (default: "sciget")
#   - user UID/GID are NOT 1000/100
#   - HOME is a host-owned bind-mount over /home/jovyan
#   - APPTAINER_CONTAINER set so is_apptainer_runtime() branches trigger
# ---------------------------------------------------------------------------

# Prepare an isolated passwd/group/home triplet for an HPC-style container.
# Sets the following globals (bash 3.2 on macOS has no namerefs, and the older
# `mapfile` builtin is not available, so we communicate via globals):
#   HPC_HOME_DIR, HPC_PASSWD_FILE, HPC_GROUP_FILE
#
# Args: USERNAME UID GID
hpc_prepare_mounts() {
    local hpc_user="$1"
    local hpc_uid="$2"
    local hpc_gid="$3"

    HPC_HOME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/neurodesktop-hpc-home.XXXXXX")"
    HPC_PASSWD_FILE="$(mktemp "${TMPDIR:-/tmp}/neurodesktop-hpc-passwd.XXXXXX")"
    HPC_GROUP_FILE="$(mktemp "${TMPDIR:-/tmp}/neurodesktop-hpc-group.XXXXXX")"

    # Build an /etc/passwd + /etc/group that mirrors Apptainer adding the host
    # user to the in-container NSS. Keep jovyan so any code that looks it up
    # still works. The host user is a distinct entry with a distinct UID (not
    # 1000) — that separation from jovyan is what exercises the HPC cross-user
    # mismatch in guacamole.sh's SFTP stamping.
    cat > "$HPC_PASSWD_FILE" <<EOF
root:x:0:0:root:/root:/bin/bash
jovyan:x:1000:100:jovyan:/home/jovyan:/bin/bash
${hpc_user}:x:${hpc_uid}:${hpc_gid}:${hpc_user} (HPC simulated):/home/jovyan:/bin/bash
nobody:x:65534:65534:nobody:/:/usr/sbin/nologin
EOF

    cat > "$HPC_GROUP_FILE" <<EOF
root:x:0:
users:x:100:jovyan,${hpc_user}
${hpc_user}:x:${hpc_gid}:
nogroup:x:65534:
EOF

    # Do NOT sudo-chown on the host. On macOS Docker Desktop, bind mounts do
    # UID translation; host chown would prompt for a password without changing
    # in-container behaviour. chmod 0777 is enough for the non-root container
    # user to populate $HOME.
    chmod 0777 "$HPC_HOME_DIR"
    chmod 0644 "$HPC_PASSWD_FILE" "$HPC_GROUP_FILE"
}

# Assemble the docker-run argv for an HPC-style launch and expose it in the
# global array HPC_DOCKER_ARGS. Does NOT include the `docker run` prefix, any
# `-d`/`-it` mode flag, port mappings, or the trailing image/command.
#
# Args: NAME USER UID GID HOME_DIR PASSWD_FILE GROUP_FILE
hpc_docker_args() {
    local name="$1" hpc_user="$2" hpc_uid="$3" hpc_gid="$4"
    local home_dir="$5" passwd_file="$6" group_file="$7"

    # NOTE: no --privileged, no --user=root. --user=<uid>:<gid> mimics
    # Apptainer's UID mapping. APPTAINER_CONTAINER makes is_apptainer_runtime()
    # return true, which switches guacamole.sh / ensure_rdp_backend.sh etc.
    # onto their unprivileged-HPC code paths.
    HPC_DOCKER_ARGS=(
        --shm-size=1gb
        --user "${hpc_uid}:${hpc_gid}"
        --name "$name"
        -v "${home_dir}:/home/jovyan"
        -v "${passwd_file}:/etc/passwd:ro"
        -v "${group_file}:/etc/group:ro"
        -v "${HOME}/neurodesktop-storage:/neurodesktop-storage"
        -e CVMFS_DISABLE=true
        # Real Apptainer sessions inherit NB_USER=jovyan from the image ENV
        # while the running UID is a nameless host user. Do NOT force
        # NB_USER=${hpc_user} here: that accidentally masks the production
        # HPC bug where guacamole.sh stamps sftp-username=jovyan into the
        # mapping while sshd is running as the host UID, aborting the VNC
        # tunnel with upstream error 515.
        -e NB_USER=jovyan
        -e NB_UID=1000
        -e NB_GID=100
        -e HOME=/home/jovyan
        -e "USER=${hpc_user}"
        -e "LOGNAME=${hpc_user}"
        -e APPTAINER_CONTAINER=1
        -e "APPTAINER_NAME=${name}"
        -e NEURODESKTOP_CVMFS_STARTUP_MODE=lazy
        # Real HPC Apptainer sets NEURODESKTOP_SLURM_MODE=host and talks to the
        # cluster's live slurmctld. Our docker simulation has no host Slurm, so
        # disable it entirely - the Slurm tests cleanly skip on SLURM_ENABLE=0.
        -e NEURODESKTOP_SLURM_ENABLE=0
        -e NEURODESKTOP_SLURM_STARTUP_MODE=eager
    )
}

# Remove an HPC simulation bind-mount home plus the passwd/group temp files.
# The container ran as UID 5000 (non-root, non-jovyan) and populated the
# bind-mounted HOME with files owned by that UID. On Linux (and in CI) the
# host user cannot `rm` those files because they are not the owner. We use
# the already-pulled neurodesktop image itself as root to chown the tree
# back to the host user first, then delete normally.
#
# Args: IMAGE HOME_DIR PASSWD_FILE GROUP_FILE
hpc_cleanup_mounts() {
    local image="$1" home_dir="$2" passwd_file="$3" group_file="$4"

    if [ -n "$home_dir" ] && [ -d "$home_dir" ]; then
        docker run --rm --user 0:0 --entrypoint chown \
            -v "$home_dir":/cleanup "$image" \
            -R "$(id -u):$(id -g)" /cleanup >/dev/null 2>&1 || true
        rm -rf "$home_dir" 2>/dev/null || true
    fi
    rm -f "$passwd_file" "$group_file" 2>/dev/null || true
}

# Wait for Jupyter to bind :8888 inside the named container. Prints docker
# logs and returns non-zero if the container exits or times out.
hpc_wait_for_ready() {
    local name="$1"
    local max_iters="${2:-90}"

    echo "Waiting for container startup (up to $((max_iters*2))s)..."
    for i in $(seq 1 "$max_iters"); do
        if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q true; then
            echo "[ERROR] Container exited during startup. Last 120 log lines:"
            docker logs --tail 120 "$name" || true
            return 1
        fi
        # Any HTTP status (including 403 "Forbidden") proves Jupyter is
        # listening. Newer jupyter-server releases gate /api/status behind
        # the auth token, so `curl -sf` (2xx-only) reports "not ready" on a
        # 403 forever. Use `-w '%{http_code}'` to capture the status.
        #
        # Gotchas this guards against:
        #   - When curl fails to connect it still prints `000` to stdout
        #     from `%{http_code}`, AND exits non-zero; the `|| echo 000`
        #     fallback then concatenates a second `000` giving `000000`.
        #   - So accept only a clean, three-digit, non-000 status.
        code=$(docker exec "$name" curl -so /dev/null -w '%{http_code}' \
                 --max-time 2 http://localhost:8888/api/status 2>/dev/null || echo 000)
        if echo "$code" | grep -Eq '^[0-9]{3}$' && [ "$code" != "000" ]; then
            echo "Container ready after ~$((i*2))s (jupyter responded HTTP ${code})"
            return 0
        fi
        sleep 2
    done

    echo "[ERROR] Container did not reach /api/status in $((max_iters*2))s. Last 120 log lines:"
    docker logs --tail 120 "$name" || true
    echo ""
    echo "Container is still running; inspect with: docker exec -it ${name} bash"
    return 1
}

# Interactive HPC session: starts the container attached to the terminal, just
# like the default bottom-of-script `docker run -it` path, but under the HPC
# simulation envelope. Ctrl-C stops the container. Cleanup is manual so the
# user can inspect afterwards.
#
# Usage: build_and_run.sh hpc [USERNAME] [UID] [GID]
if [ "${1:-}" = "hpc" ]; then
    HPC_USER="${2:-sciget}"
    HPC_UID="${3:-5000}"
    HPC_GID="${4:-5000}"
    name="neurodesktop-hpc"

    # Sets HPC_HOME_DIR / HPC_PASSWD_FILE / HPC_GROUP_FILE.
    hpc_prepare_mounts "$HPC_USER" "$HPC_UID" "$HPC_GID"

    echo "============================================================"
    echo "HPC simulation (interactive): user=${HPC_USER} uid=${HPC_UID} gid=${HPC_GID}"
    echo "  home:    ${HPC_HOME_DIR}"
    echo "  passwd:  ${HPC_PASSWD_FILE}"
    echo "  group:   ${HPC_GROUP_FILE}"
    echo "  no --privileged, no --user=root, no sudo"
    echo "  Jupyter: http://127.0.0.1:8888/"
    echo "  Cleanup: docker rm -f ${name} && rm -rf ${HPC_HOME_DIR} ${HPC_PASSWD_FILE} ${HPC_GROUP_FILE}"
    echo "============================================================"

    docker rm -f "$name" 2>/dev/null || true

    # Populates HPC_DOCKER_ARGS; bash 3.2-compatible replacement for mapfile.
    hpc_docker_args "$name" "$HPC_USER" "$HPC_UID" "$HPC_GID" \
        "$HPC_HOME_DIR" "$HPC_PASSWD_FILE" "$HPC_GROUP_FILE"
    docker run -it "${HPC_DOCKER_ARGS[@]}" \
        -p 127.0.0.1:8888:8888 \
        neurodesktop:latest
    exit $?
fi

# Detached HPC session + run pytest inside. Container is left running so the
# user can `docker exec -it neurodesktop-hpctest bash` for further inspection.
#
# Usage: build_and_run.sh hpctest [USERNAME] [UID] [GID]
if [ "${1:-}" = "hpctest" ]; then
    HPC_USER="${2:-sciget}"
    HPC_UID="${3:-5000}"
    HPC_GID="${4:-5000}"
    name="neurodesktop-hpctest"

    hpc_prepare_mounts "$HPC_USER" "$HPC_UID" "$HPC_GID"

    echo "============================================================"
    echo "HPC simulation (tests): user=${HPC_USER} uid=${HPC_UID} gid=${HPC_GID}"
    echo "  home:    ${HPC_HOME_DIR}"
    echo "  passwd:  ${HPC_PASSWD_FILE}"
    echo "  group:   ${HPC_GROUP_FILE}"
    echo "  no --privileged, no --user=root, no sudo"
    echo "============================================================"

    docker rm -f "$name" 2>/dev/null || true

    hpc_docker_args "$name" "$HPC_USER" "$HPC_UID" "$HPC_GID" \
        "$HPC_HOME_DIR" "$HPC_PASSWD_FILE" "$HPC_GROUP_FILE"
    docker run -d "${HPC_DOCKER_ARGS[@]}" \
        -p 127.0.0.1:8888:8888 \
        neurodesktop:latest >/dev/null

    if ! hpc_wait_for_ready "$name"; then
        # Tear down the dead/stuck container and temp state before bailing.
        docker rm -f "$name" >/dev/null 2>&1 || true
        hpc_cleanup_mounts neurodesktop:latest \
            "$HPC_HOME_DIR" "$HPC_PASSWD_FILE" "$HPC_GROUP_FILE"
        exit 1
    fi

    echo ""
    echo "============================================================"
    echo "Running tests as ${HPC_USER} (UID ${HPC_UID}) inside the container"
    echo "============================================================"
    # Disable `set -e` for the test step so a non-zero pytest exit code does
    # not skip the tear-down below.
    set +e
    docker exec "$name" pytest /opt/tests/ -v
    result=$?
    set -e

    echo ""
    echo "============================================================"
    echo "Cleaning up HPC test container + temp files..."
    echo "============================================================"
    docker rm -f "$name" >/dev/null 2>&1 || true
    hpc_cleanup_mounts neurodesktop:latest \
        "$HPC_HOME_DIR" "$HPC_PASSWD_FILE" "$HPC_GROUP_FILE"
    exit $result
fi

if [ "${1:-}" = "fulltest" ]; then
    echo "============================================================"
    echo "Running tests across all configurations (parallel)"
    echo "  (use './build_and_run.sh fulltest_verbose' to stream output)"
    echo "============================================================"

    # Configuration format: "kind:arg1:arg2"
    #   kind=std  -> classic docker: cvmfs_disable:grant_sudo
    #   kind=hpc  -> apptainer-style: non-root, non-jovyan, no privileged
    CONFIGS=(
        "std:false:no"
        "std:false:yes"
        "std:true:no"
        "std:true:yes"
        "hpc:sciget:5000:5000"
    )
    PIDS=()
    NAMES=()
    LABELS=()
    KINDS=()
    # Per-config cleanup paths for HPC entries.
    HPC_HOMES=()
    HPC_PASSWDS=()
    HPC_GROUPS=()
    LOGDIR=$(mktemp -d)

    # Start all containers in parallel
    for i in "${!CONFIGS[@]}"; do
        IFS=':' read -r kind a b c <<< "${CONFIGS[$i]}"
        name="neurodesktop-test-${i}"

        docker rm -f "$name" 2>/dev/null || true

        case "$kind" in
            std)
                cvmfs_disable="$a"
                grant_sudo="$b"
                label="std CVMFS_DISABLE=${cvmfs_disable} GRANT_SUDO=${grant_sudo}"
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
                HPC_HOMES+=("")
                HPC_PASSWDS+=("")
                HPC_GROUPS+=("")
                ;;
            hpc)
                hpc_user="$a"
                hpc_uid="$b"
                hpc_gid="$c"
                label="hpc user=${hpc_user} uid=${hpc_uid} (no root, no privileged, no sudo)"
                echo "Starting: ${label} (${name})"
                hpc_prepare_mounts "$hpc_user" "$hpc_uid" "$hpc_gid"
                HPC_HOMES+=("$HPC_HOME_DIR")
                HPC_PASSWDS+=("$HPC_PASSWD_FILE")
                HPC_GROUPS+=("$HPC_GROUP_FILE")
                hpc_docker_args "$name" "$hpc_user" "$hpc_uid" "$hpc_gid" \
                    "$HPC_HOME_DIR" "$HPC_PASSWD_FILE" "$HPC_GROUP_FILE"
                docker run -d "${HPC_DOCKER_ARGS[@]}" neurodesktop:latest >/dev/null
                ;;
        esac

        NAMES+=("$name")
        LABELS+=("$label")
        KINDS+=("$kind")
    done

    # Wait for all containers to be ready, then run tests in parallel. Output
    # is captured per-config to a log file and dumped at the end - this hides
    # live progress but finishes roughly 5x faster than the sequential path.
    # If you want to see progress in real time, use `fulltest_verbose` below.
    for i in "${!CONFIGS[@]}"; do
        name="${NAMES[$i]}"
        label="${LABELS[$i]}"
        kind="${KINDS[$i]}"
        logfile="${LOGDIR}/${name}.log"

        if [ "$kind" = "hpc" ]; then
            pytest_exec=(docker exec "$name" pytest /opt/tests/ -v)
        else
            pytest_exec=(docker exec -u jovyan "$name" pytest /opt/tests/ -v)
        fi

        (
            for attempt in $(seq 1 90); do
                if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q true; then
                    echo "[ERROR] Container exited before reaching readiness."
                    docker logs --tail 120 "$name" || true
                    exit 1
                fi
                code=$(docker exec "$name" curl -so /dev/null -w '%{http_code}' \
                         --max-time 2 http://localhost:8888/api/status 2>/dev/null || echo 000)
                if echo "$code" | grep -Eq '^[0-9]{3}$' && [ "$code" != "000" ]; then
                    break
                fi
                sleep 2
            done

            echo "============================================================"
            echo "Config: ${label}"
            echo "============================================================"
            "${pytest_exec[@]}"
        ) > "$logfile" 2>&1 &
        PIDS+=($!)
    done

    echo "Waiting for all test runs to complete (running in parallel)..."

    FAILED=0
    for i in "${!CONFIGS[@]}"; do
        name="${NAMES[$i]}"
        label="${LABELS[$i]}"
        kind="${KINDS[$i]}"
        logfile="${LOGDIR}/${name}.log"

        wait "${PIDS[$i]}" && status="PASSED" || { status="FAILED"; FAILED=1; }

        echo ""
        echo "============================================================"
        echo "${status}: ${label}"
        echo "============================================================"
        cat "$logfile"

        docker rm -f "$name" 2>/dev/null || true
        if [ "$kind" = "std" ]; then
            docker volume rm "${name}-home" 2>/dev/null || true
        else
            hpc_cleanup_mounts neurodesktop:latest \
                "${HPC_HOMES[$i]}" "${HPC_PASSWDS[$i]}" "${HPC_GROUPS[$i]}"
        fi
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

if [ "${1:-}" = "fulltest_verbose" ]; then
    echo "============================================================"
    echo "Running tests across all configurations (sequential, live output)"
    echo "============================================================"

    # Configuration format: "kind:arg1:arg2"
    #   kind=std  -> classic docker: cvmfs_disable:grant_sudo
    #   kind=hpc  -> apptainer-style: non-root, non-jovyan, no privileged
    CONFIGS=(
        "std:false:no"
        "std:false:yes"
        "std:true:no"
        "std:true:yes"
        "hpc:sciget:5000:5000"
    )

    FAILED=0
    RESULTS=()

    # Run each configuration one after the other so output streams live to the
    # terminal. Previously all five ran in parallel with output captured to
    # per-config log files and dumped only at the end, which felt like the
    # script was hanging. Parallelism is not worth the opacity here.
    for i in "${!CONFIGS[@]}"; do
        IFS=':' read -r kind a b c <<< "${CONFIGS[$i]}"
        name="neurodesktop-test-${i}"
        docker rm -f "$name" 2>/dev/null || true

        config_hpc_home=""
        config_hpc_passwd=""
        config_hpc_group=""

        case "$kind" in
            std)
                cvmfs_disable="$a"
                grant_sudo="$b"
                label="std CVMFS_DISABLE=${cvmfs_disable} GRANT_SUDO=${grant_sudo}"
                echo ""
                echo "============================================================"
                echo "[$((i+1))/${#CONFIGS[@]}] Starting: ${label}"
                echo "============================================================"
                docker volume create "${name}-home" 2>/dev/null || true
                docker run -d --shm-size=1gb --privileged --user=root \
                    --name "$name" \
                    --mount "source=${name}-home,target=/home/jovyan" \
                    -v ~/neurodesktop-storage:/neurodesktop-storage \
                    -e CVMFS_DISABLE="${cvmfs_disable}" \
                    -e GRANT_SUDO="${grant_sudo}" \
                    -e NEURODESKTOP_CVMFS_STARTUP_MODE=eager \
                    -e NB_UID="$(id -u)" -e NB_GID="$(id -g)" \
                    neurodesktop:latest >/dev/null
                pytest_exec=(docker exec -u jovyan "$name" pytest /opt/tests/ -v)
                ;;
            hpc)
                hpc_user="$a"
                hpc_uid="$b"
                hpc_gid="$c"
                label="hpc user=${hpc_user} uid=${hpc_uid} (no root, no privileged, no sudo)"
                echo ""
                echo "============================================================"
                echo "[$((i+1))/${#CONFIGS[@]}] Starting: ${label}"
                echo "============================================================"
                hpc_prepare_mounts "$hpc_user" "$hpc_uid" "$hpc_gid"
                config_hpc_home="$HPC_HOME_DIR"
                config_hpc_passwd="$HPC_PASSWD_FILE"
                config_hpc_group="$HPC_GROUP_FILE"
                hpc_docker_args "$name" "$hpc_user" "$hpc_uid" "$hpc_gid" \
                    "$HPC_HOME_DIR" "$HPC_PASSWD_FILE" "$HPC_GROUP_FILE"
                docker run -d "${HPC_DOCKER_ARGS[@]}" neurodesktop:latest >/dev/null
                pytest_exec=(docker exec "$name" pytest /opt/tests/ -v)
                ;;
        esac

        echo "Waiting for container startup..."
        ready=0
        for attempt in $(seq 1 90); do
            if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q true; then
                echo "[ERROR] Container exited before reaching readiness:"
                docker logs --tail 120 "$name" || true
                break
            fi
            code=$(docker exec "$name" curl -so /dev/null -w '%{http_code}' \
                     --max-time 2 http://localhost:8888/api/status 2>/dev/null || echo 000)
            if echo "$code" | grep -Eq '^[0-9]{3}$' && [ "$code" != "000" ]; then
                ready=1
                echo "Container ready after ~$((attempt*2))s (HTTP ${code})"
                break
            fi
            sleep 2
        done

        # Run pytest with live output. Disable `set -e` around it so failure
        # proceeds to teardown rather than aborting the whole script.
        if [ "$ready" -eq 1 ]; then
            set +e
            "${pytest_exec[@]}"
            rc=$?
            set -e
        else
            rc=1
        fi

        if [ $rc -eq 0 ]; then
            status="PASSED"
        else
            status="FAILED"
            FAILED=1
        fi
        RESULTS+=("${status}: ${label}")

        echo ""
        echo "------------------------------------------------------------"
        echo "${status} (rc=${rc}): ${label}"
        echo "------------------------------------------------------------"

        # Teardown this config before moving to the next.
        docker rm -f "$name" >/dev/null 2>&1 || true
        if [ "$kind" = "std" ]; then
            docker volume rm "${name}-home" >/dev/null 2>&1 || true
        else
            hpc_cleanup_mounts neurodesktop:latest \
                "$config_hpc_home" "$config_hpc_passwd" "$config_hpc_group"
        fi
    done

    echo ""
    echo "============================================================"
    echo "SUMMARY"
    echo "============================================================"
    for line in "${RESULTS[@]}"; do
        echo "  ${line}"
    done
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
