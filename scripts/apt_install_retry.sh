#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: apt-install-retry <package> [<package> ...]" >&2
  exit 2
fi

max_attempts="${APT_INSTALL_RETRY_ATTEMPTS:-3}"
case "${max_attempts}" in
  ''|*[!0-9]*|0)
    echo "APT_INSTALL_RETRY_ATTEMPTS must be a positive integer; got '${max_attempts}'." >&2
    exit 2
    ;;
esac

attempt=1
while [ "${attempt}" -le "${max_attempts}" ]; do
  if rm -rf /var/lib/apt/lists/* \
    && apt-get update -o APT::Update::Error-Mode=any -o Acquire::Retries=5 --yes \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends "$@"; then
    exit 0
  else
    rc=$?
  fi

  if [ "${attempt}" -eq "${max_attempts}" ]; then
    echo "apt-install-retry: failed after ${max_attempts} attempts." >&2
    exit "${rc}"
  fi

  echo "apt-install-retry: attempt ${attempt}/${max_attempts} failed with exit ${rc}; refreshing apt indexes and retrying." >&2
  apt-get clean || true
  sleep "$((attempt * 10))"
  attempt="$((attempt + 1))"
done
