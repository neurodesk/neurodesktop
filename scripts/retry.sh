#!/usr/bin/env bash
# Generic retry-with-exponential-backoff wrapper for flaky network commands
# (e.g. `curl ... | bash` installers, `conda install`, `git clone`) that fetch
# from external CDNs with no built-in retry. Mirrors apt-install-retry, but for
# arbitrary commands.
#
# Usage:
#   retry <command> [args...]
#   retry bash -o pipefail -c 'curl -fsSL https://example/install | bash'
#
# Tunables (env): RETRY_ATTEMPTS (default 5), RETRY_DELAY (initial seconds, default 8).
set -uo pipefail

attempts="${RETRY_ATTEMPTS:-5}"
delay="${RETRY_DELAY:-8}"
n=1

while true; do
    if "$@"; then
        exit 0
    fi
    rc=$?
    if [ "$n" -ge "$attempts" ]; then
        echo "retry: '$*' failed after ${n} attempts (exit ${rc}); giving up." >&2
        exit "$rc"
    fi
    echo "retry: attempt ${n}/${attempts} of '$*' failed (exit ${rc}); retrying in ${delay}s..." >&2
    sleep "$delay"
    n=$((n + 1))
    delay=$((delay * 2))
done
