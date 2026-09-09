#!/usr/bin/env bash

# Validate the desired Neurodesk container inventory against one mounted CVMFS
# snapshot. Keep this independent of client setup so both health-check jobs can
# share the behavior and the checkout unit tests can exercise it without CVMFS.
set -euo pipefail

INVENTORY_URL="${CVMFS_INVENTORY_URL:-https://raw.githubusercontent.com/NeuroDesk/neurocommand/main/cvmfs/log.txt}"
REPOSITORY_ROOT="${CVMFS_REPOSITORY_ROOT:-/cvmfs/neurodesk.ardc.edu.au}"

inventory_file=$(mktemp) || {
    echo "ERROR: could not create a temporary CVMFS inventory file."
    exit 1
}
trap 'rm -f "$inventory_file"' EXIT

if ! wget --quiet --output-document="$inventory_file" "$INVENTORY_URL"; then
    echo "ERROR: could not download CVMFS inventory from $INVENTORY_URL."
    exit 1
fi
if [[ ! -s "$inventory_file" ]]; then
    echo "ERROR: CVMFS inventory download was empty: $INVENTORY_URL"
    exit 1
fi

entry_count=0
invalid_count=0
missing_count=0
while IFS= read -r line || [[ -n "$line" ]]; do
    image="${line%%[[:space:]]*}"
    [[ -n "$image" ]] || continue

    # Container identifiers become path components below. Treat malformed
    # publisher metadata as a failed health check instead of following it.
    case "$image" in
        *[!A-Za-z0-9._+-]*)
            echo "ERROR: invalid container identifier in CVMFS inventory: $image"
            invalid_count=$((invalid_count + 1))
            continue
            ;;
    esac

    entry_count=$((entry_count + 1))
    commands_file="$REPOSITORY_ROOT/containers/$image/commands.txt"
    if [[ ! -f "$commands_file" ]]; then
        echo "ERROR: CVMFS inventory entry is missing commands.txt: $image"
        echo "::error title=CVMFS inventory mismatch::$image is missing from the mounted repository"
        missing_count=$((missing_count + 1))
    fi
done < "$inventory_file"

if [[ "$entry_count" -eq 0 ]]; then
    echo "ERROR: CVMFS inventory did not contain any container entries."
    exit 1
fi
if [[ "$invalid_count" -ne 0 ]]; then
    echo "ERROR: $invalid_count invalid CVMFS inventory entries were found."
    exit 1
fi
if [[ "$missing_count" -ne 0 ]]; then
    echo "ERROR: $missing_count of $entry_count CVMFS inventory entries are missing."
    exit 2
fi

echo "Validated $entry_count CVMFS inventory entries."
