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

if [ "$#" -eq 0 ]; then
    echo "usage: retry <command> [args ...]" >&2
    exit 2
fi

attempts="${RETRY_ATTEMPTS:-5}"
delay="${RETRY_DELAY:-8}"
case "$attempts" in
    ''|*[!0-9]*)
        echo "RETRY_ATTEMPTS must be a positive integer; got '${attempts}'." >&2
        exit 2
        ;;
esac
case "$delay" in
    ''|*[!0-9]*)
        echo "RETRY_DELAY must be a non-negative integer; got '${delay}'." >&2
        exit 2
        ;;
esac

# Force decimal interpretation so values such as 08 remain valid shell
# integers when the delay is doubled below.
attempts_input="$attempts"
attempts="$((10#$attempts))"
delay="$((10#$delay))"
if [ "$attempts" -eq 0 ]; then
    echo "RETRY_ATTEMPTS must be a positive integer; got '${attempts_input}'." >&2
    exit 2
fi
n=1

while true; do
    if "$@"; then
        exit 0
    else
        rc=$?
    fi
    if [ "$n" -ge "$attempts" ]; then
        echo "retry: '$*' failed after ${n} attempts (exit ${rc}); giving up." >&2
        exit "$rc"
    fi
    echo "retry: attempt ${n}/${attempts} of '$*' failed (exit ${rc}); retrying in ${delay}s..." >&2
    sleep "$delay"
    n=$((n + 1))
    delay=$((delay * 2))
done
