#!/usr/bin/env bash
set -euo pipefail

destination=/opt/neurodesktop/slurm-env
if [ "$(/usr/sbin/slurmd --version)" != 'slurm-wlm 25.11.2' ]; then
    echo 'The Slurm environment patch requires Ubuntu Slurm 25.11.2.' >&2
    exit 1
fi
library=$(ldd /usr/sbin/slurmd | awk '$1 == "libslurmfull.so" {print $3}')
test -f "$library"
install -m 0755 /usr/sbin/slurmd "$destination/slurmd"
"${1:-patchelf}" --add-needed "$destination/user-env.so" "$destination/slurmd"
sha256sum /usr/sbin/slurmd "$library" > "$destination/compatibility.sha256"
test "$("$destination/slurmd" --version)" = 'slurm-wlm 25.11.2'
