#!/usr/bin/env bash

set -euo pipefail

INVENTORY_URL="${OBJECT_STORAGE_INVENTORY_URL:-https://raw.githubusercontent.com/NeuroDesk/neurocommand/main/cvmfs/log.txt}"
CHECK_ATTEMPTS="${OBJECT_STORAGE_CHECK_ATTEMPTS:-3}"
RETRY_DELAY="${OBJECT_STORAGE_RETRY_DELAY:-30}"
SERVER_ADDRESS="${1:-}"

if [[ -z "$SERVER_ADDRESS" ]]; then
    echo "ERROR: an object-storage server address is required."
    exit 1
fi
SERVER_ADDRESS="${SERVER_ADDRESS%/}/"

if [[ ! "$CHECK_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: OBJECT_STORAGE_CHECK_ATTEMPTS must be a positive integer."
    exit 1
fi
if [[ ! "$RETRY_DELAY" =~ ^[0-9]+$ ]]; then
    echo "ERROR: OBJECT_STORAGE_RETRY_DELAY must be a non-negative integer."
    exit 1
fi

inventory_file=$(mktemp) || {
    echo "ERROR: could not create a temporary object-storage inventory file."
    exit 1
}
trap 'rm -f "$inventory_file"' EXIT

if ! wget --quiet --output-document="$inventory_file" "$INVENTORY_URL"; then
    echo "ERROR: could not download object-storage inventory from $INVENTORY_URL."
    exit 1
fi
if [[ ! -s "$inventory_file" ]]; then
    echo "ERROR: object-storage inventory download was empty: $INVENTORY_URL"
    exit 1
fi

images=()
invalid_count=0
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -n "$line" ]] || continue
    image="${line%%[[:space:]]*}"

    case "$image" in
        ""|.|..|*[!A-Za-z0-9._+-]*)
            echo "ERROR: invalid container identifier in object-storage inventory: $line"
            invalid_count=$((invalid_count + 1))
            continue
            ;;
    esac

    images+=("$image")
done < "$inventory_file"

entry_count=${#images[@]}
if [[ "$entry_count" -eq 0 ]]; then
    echo "ERROR: object-storage inventory did not contain any container entries."
    exit 1
fi
if [[ "$invalid_count" -ne 0 ]]; then
    echo "ERROR: $invalid_count invalid object-storage inventory entries were found."
    exit 1
fi

missing_images=()
find_missing_images() {
    local curl_status
    local http_code
    local image
    local -a candidates=("$@")

    missing_images=()
    for image in "${candidates[@]}"; do
        curl_status=0
        http_code=$(curl \
            --connect-timeout 10 \
            --max-time 30 \
            --output /dev/null \
            --silent \
            --head \
            --fail \
            --write-out "%{http_code}" \
            "${SERVER_ADDRESS}${image}.simg") || curl_status=$?
        if [[ "$curl_status" -eq 0 && "$http_code" == "200" ]]; then
            echo "[DEBUG] ${image}.simg exists in $SERVER_ADDRESS"
        elif [[ "$http_code" == "404" ]]; then
            missing_images+=("$image")
        else
            echo "ERROR: object-storage request failed for ${image}.simg (curl status $curl_status, HTTP status ${http_code:-unavailable})."
            return 1
        fi
    done
}

find_missing_images "${images[@]}"
attempt=1
while [[ "${#missing_images[@]}" -ne 0 && "$attempt" -lt "$CHECK_ATTEMPTS" ]]; do
    retry_count=${#missing_images[@]}
    retry_noun="entries"
    [[ "$retry_count" -ne 1 ]] || retry_noun="entry"
    next_attempt=$((attempt + 1))
    echo "WARNING: retrying $retry_count missing $retry_noun after $RETRY_DELAY seconds (attempt $next_attempt/$CHECK_ATTEMPTS)."
    sleep "$RETRY_DELAY"
    retry_images=("${missing_images[@]}")
    find_missing_images "${retry_images[@]}"
    attempt=$next_attempt
done

missing_count=${#missing_images[@]}
if [[ "$missing_count" -ne 0 ]]; then
    for image in "${missing_images[@]}"; do
        echo "ERROR: object-storage inventory entry is missing: ${image}.simg"
        echo "::error title=Object-storage inventory mismatch::${image}.simg is missing from $SERVER_ADDRESS"
    done
    echo "ERROR: $missing_count of $entry_count object-storage inventory entries are missing."
    exit 2
fi

echo "Validated $entry_count object-storage inventory entries at $SERVER_ADDRESS"
