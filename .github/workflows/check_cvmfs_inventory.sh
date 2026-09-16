#!/usr/bin/env bash

set -euo pipefail

INVENTORY_URL="${CVMFS_INVENTORY_URL:-https://raw.githubusercontent.com/NeuroDesk/neurocommand/main/cvmfs/log.txt}"
REPOSITORY_ROOT="${CVMFS_REPOSITORY_ROOT:-/cvmfs/neurodesk.ardc.edu.au}"
REPOSITORY_NAME="${CVMFS_REPOSITORY_NAME:-$(basename "$REPOSITORY_ROOT")}"

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

images=()
invalid_count=0
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -n "$line" ]] || continue
    image="${line%%[[:space:]]*}"

    case "$image" in
        ""|.|..|*[!A-Za-z0-9._+-]*)
            echo "ERROR: invalid container identifier in CVMFS inventory: $line"
            invalid_count=$((invalid_count + 1))
            continue
            ;;
    esac

    images+=("$image")
done < "$inventory_file"

entry_count=${#images[@]}
if [[ "$entry_count" -eq 0 ]]; then
    echo "ERROR: CVMFS inventory did not contain any container entries."
    exit 1
fi
if [[ "$invalid_count" -ne 0 ]]; then
    echo "ERROR: $invalid_count invalid CVMFS inventory entries were found."
    exit 1
fi

missing_images=()
find_missing_images() {
    local image
    local commands_file

    missing_images=()
    for image in "${images[@]}"; do
        commands_file="$REPOSITORY_ROOT/containers/$image/commands.txt"
        if [[ ! -f "$commands_file" ]]; then
            missing_images+=("$image")
        fi
    done
}

# The health jobs run on Linux with GNU timeout and passwordless sudo.
# Diagnostics are best effort; a failed refresh never hides omissions.
catalog_status() {
    timeout --kill-after=5s 10s cvmfs_config stat -v "$REPOSITORY_NAME" || true
}

refresh_catalog() {
    local -a prefix=()
    if [[ "$EUID" -ne 0 ]]; then
        prefix=(sudo -n)
    fi
    "${prefix[@]}" timeout --kill-after=5s 45s \
        cvmfs_talk -i "$REPOSITORY_NAME" remount sync
}

find_missing_images
if [[ "${#missing_images[@]}" -ne 0 ]]; then
    echo "WARNING: ${#missing_images[@]} CVMFS inventory entries were not visible; refreshing the catalog before the final check."
    catalog_status
    if ! refresh_catalog; then
        echo "WARNING: CVMFS catalog refresh failed or timed out; checking the same inventory again."
    fi
    catalog_status
    find_missing_images
fi

missing_count=${#missing_images[@]}
if [[ "$missing_count" -ne 0 ]]; then
    for image in "${missing_images[@]}"; do
        echo "ERROR: CVMFS inventory entry is missing commands.txt: $image"
        echo "::error title=CVMFS inventory mismatch::$image is missing from the mounted repository"
    done
    echo "ERROR: $missing_count of $entry_count CVMFS inventory entries are missing."
    exit 2
fi

echo "Validated $entry_count CVMFS inventory entries."
