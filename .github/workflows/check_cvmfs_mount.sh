#!/usr/bin/env bash

set -u

repository_root="${CVMFS_REPOSITORY_ROOT:-/cvmfs/neurodesk.ardc.edu.au}"
repository_name="${CVMFS_REPOSITORY_NAME:-neurodesk.ardc.edu.au}"

verification_started=$SECONDS
verification_deadline=$((verification_started + 120))

verify_command() {
    local limit=$1
    local remaining=$((verification_deadline - SECONDS - 5))
    shift
    (( remaining > 0 )) || return 124
    (( limit <= remaining )) || limit=$remaining
    timeout --kill-after=5s "${limit}s" "$@"
}

for attempt in 1 2; do
    # Access triggers autofs; its trigger alone is not a mounted CVMFS repository.
    verify_command 45 ls "$repository_root" >/dev/null
    read_status=$?
    findmnt_status=not_run
    if (( read_status == 0 )); then
        verify_command 10 findmnt --noheadings --mountpoint "$repository_root" --types fuse,fuse.cvmfs --source cvmfs2
        findmnt_status=$?
    fi
    elapsed=$((SECONDS - verification_started))
    remaining=$((verification_deadline - SECONDS))
    echo "[INFO]: CVMFS attempt=$attempt read_status=$read_status findmnt_status=$findmnt_status elapsed=${elapsed}s remaining=${remaining}s"
    if [[ "$read_status" == 0 && "$findmnt_status" == 0 ]]; then
        if (( attempt > 1 )); then
            echo "[INFO]: CVMFS mount recovered on attempt $attempt."
        fi
        exit 0
    fi
    if (( attempt == 1 )); then
        echo "::warning title=CVMFS mount pending::First mount verification failed; retrying the same repository within the remaining verification budget."
    fi
done

echo "::error title=CVMFS mount unavailable::Cannot read a mounted CVMFS repository at $repository_root; inventory comparison was not run."

diagnostic() {
    echo "[DEBUG]: $*"
    sudo -n timeout --kill-after=5s 10s "$@" || true
}

diagnostic cvmfs_config showconfig "$repository_name"
diagnostic cvmfs_config probe "$repository_name"
diagnostic cvmfs_config stat -v "$repository_name"
diagnostic systemctl --no-pager --full status autofs
diagnostic journalctl --no-pager -n 100 -u autofs
diagnostic journalctl --no-pager -n 100 -t cvmfs2
diagnostic tail -n 100 /var/log/syslog
exit 1
