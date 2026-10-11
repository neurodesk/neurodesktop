#!/usr/bin/env bash

set -u

repository_root="${CVMFS_REPOSITORY_ROOT:-/cvmfs/neurodesk.ardc.edu.au}"
repository_name="${CVMFS_REPOSITORY_NAME:-neurodesk.ardc.edu.au}"

# Access triggers autofs; an ordinary directory or autofs trigger is not a CVMFS mount.
if timeout --kill-after=5s 45s ls "$repository_root" >/dev/null &&
    timeout --kill-after=5s 10s findmnt --noheadings --mountpoint "$repository_root" --types fuse.cvmfs; then
    exit 0
fi

echo "::error title=CVMFS mount unavailable::Cannot read a mounted CVMFS repository at $repository_root; inventory comparison was not run."

diagnostic() {
    echo "[DEBUG]: $*"
    timeout --kill-after=5s 10s sudo -n "$@" || true
}

diagnostic cvmfs_config showconfig "$repository_name"
diagnostic cvmfs_config probe "$repository_name"
diagnostic cvmfs_config stat -v "$repository_name"
diagnostic systemctl --no-pager --full status autofs
diagnostic journalctl --no-pager -n 100 -u autofs
diagnostic journalctl --no-pager -n 100 -t cvmfs2
diagnostic tail -n 100 /var/log/syslog
exit 1
