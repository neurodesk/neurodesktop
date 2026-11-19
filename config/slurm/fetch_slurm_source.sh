#!/usr/bin/env bash
# Download a Slurm release tarball and verify it against the pinned SHA-256.
# SchedMD's download server is the primary source. SchedMD also attaches the
# same release tarball to its GitHub releases, which serves as a fallback when
# the download server is unreachable. Each source must yield the pinned file.
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: $0 VERSION SHA256 OUTPUT" >&2
    exit 2
fi

version=$1
sha256=$2
output=$3

if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Invalid Slurm version: ${version}" >&2
    exit 2
fi
if [[ ! "$sha256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Invalid SHA-256: ${sha256}" >&2
    exit 2
fi

archive="slurm-${version}.tar.bz2"
sources=(
    "https://download.schedmd.com/slurm/${archive}"
    "https://github.com/SchedMD/slurm/releases/download/slurm-${version//./-}-1/${archive}"
)

partial="${output}.partial"
trap 'rm -f "$partial"' EXIT

for url in "${sources[@]}"; do
    rm -f "$partial"
    if ! curl -fsSL --retry 5 "$url" -o "$partial"; then
        echo "Slurm source unavailable from ${url}; trying the next source." >&2
        continue
    fi
    # A downloaded file that does not match the pin fails the build rather than
    # moving on, so an altered upstream archive is reported instead of masked.
    if ! printf '%s  %s\n' "$sha256" "$partial" | sha256sum --check --strict >&2; then
        echo "Slurm source from ${url} does not match the pinned SHA-256." >&2
        exit 1
    fi
    mv "$partial" "$output"
    echo "Fetched ${archive} from ${url}" >&2
    exit 0
done

echo "Slurm source ${archive} is unavailable from every source." >&2
exit 1
