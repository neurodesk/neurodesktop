#!/usr/bin/env bash

set -euo pipefail

INVENTORY_URL="${OBJECT_STORAGE_INVENTORY_URL:-}"
CATALOG_URL="${OBJECT_STORAGE_CATALOG_URL:-https://apps-catalog.scigetorg.workers.dev/domains.json}"
CATALOG_USER_AGENT="${OBJECT_STORAGE_CATALOG_USER_AGENT:-neurodesk-objectstorage-check}"
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

workdir=$(mktemp -d) || {
    echo "ERROR: could not create a temporary object-storage inventory directory."
    exit 1
}
trap 'rm -rf "$workdir"' EXIT
inventory_file="$workdir/inventory.txt"

if [[ -n "$INVENTORY_URL" ]]; then
    if ! wget --quiet --output-document="$inventory_file" "$INVENTORY_URL"; then
        echo "ERROR: could not download object-storage inventory from $INVENTORY_URL."
        exit 1
    fi
    if [[ ! -s "$inventory_file" ]]; then
        echo "ERROR: object-storage inventory download was empty: $INVENTORY_URL"
        exit 1
    fi
else
    domains_file="$workdir/domains.json"
    if ! wget --quiet --timeout=30 --tries=3 --user-agent="$CATALOG_USER_AGENT" \
        --output-document="$domains_file" "$CATALOG_URL"; then
        echo "ERROR: could not download object-storage inventory from $CATALOG_URL."
        exit 1
    fi
    if [[ ! -s "$domains_file" ]]; then
        echo "ERROR: object-storage inventory download was empty: $CATALOG_URL"
        exit 1
    fi

    domains_output=$(python3 - "$domains_file" <<'PY'
import json
import re
import sys

DOMAIN_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
except Exception as exc:
    print(f"ERROR: invalid object-storage catalog domains.json: {exc}")
    raise SystemExit(1)

domains = doc.get("domains") if isinstance(doc, dict) else None
if not isinstance(domains, dict) or not domains:
    print("ERROR: object-storage catalog domains.json has no configured domains.")
    raise SystemExit(1)

for domain in sorted(domains):
    if not isinstance(domain, str) or not DOMAIN_RE.fullmatch(domain):
        print(f"ERROR: invalid domain in object-storage catalog: {domain!r}")
        raise SystemExit(1)
    print(domain)
PY
    ) || {
        echo "$domains_output"
        exit 1
    }
    mapfile -t domains <<< "$domains_output"

    catalog_base="${CATALOG_URL%/*}"
    domain_files=()
    for domain in "${domains[@]}"; do
        [[ -n "$domain" ]] || continue
        domain_url="${catalog_base}/${domain}/apps.json"
        domain_file="$workdir/${domain}.apps.json"
        if ! wget --quiet --timeout=30 --tries=3 --user-agent="$CATALOG_USER_AGENT" \
            --output-document="$domain_file" "$domain_url"; then
            echo "ERROR: could not download object-storage inventory from $domain_url."
            exit 1
        fi
        if [[ ! -s "$domain_file" ]]; then
            echo "ERROR: object-storage inventory download was empty: $domain_url"
            exit 1
        fi
        domain_files+=("$domain_file")
    done

    python3 - "$inventory_file" "${domain_files[@]}" <<'PY'
import json
import re
import sys
from pathlib import Path

out_path = Path(sys.argv[1])
catalog_paths = sys.argv[2:]
IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def is_primary_container_app(menu_name: str, app_name: str) -> bool:
    if "-" not in app_name:
        return True
    parts = app_name.rsplit(" ", 1)
    return len(parts) == 2 and parts[0] == menu_name


images = set()
for raw_path in catalog_paths:
    try:
        catalog = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: invalid object-storage domain catalog {raw_path}: {exc}")
        raise SystemExit(1)
    if not isinstance(catalog, dict):
        print(f"ERROR: invalid object-storage domain catalog {raw_path}: expected object.")
        raise SystemExit(1)
    for menu_name, menu_data in catalog.items():
        if not isinstance(menu_name, str) or not isinstance(menu_data, dict):
            print(f"ERROR: invalid container entry in object-storage catalog: {menu_name!r}")
            raise SystemExit(1)
        apps = menu_data.get("apps")
        if not isinstance(apps, dict):
            print(f"ERROR: invalid apps mapping in object-storage catalog for {menu_name!r}")
            raise SystemExit(1)
        for app_name, app_data in apps.items():
            if not isinstance(app_name, str) or not isinstance(app_data, dict):
                print(f"ERROR: invalid app entry in object-storage catalog: {app_name!r}")
                raise SystemExit(1)
            if not is_primary_container_app(menu_name, app_name):
                continue
            version = app_data.get("version")
            if not isinstance(version, str) or not version or any(ch.isspace() for ch in version):
                print(f"ERROR: invalid container version in object-storage catalog for {app_name!r}: {version!r}")
                raise SystemExit(1)
            image_id = f"{app_name.replace(' ', '_')}_{version}"
            if image_id in {".", ".."} or not IMAGE_ID_RE.fullmatch(image_id):
                print(f"ERROR: invalid container identifier in object-storage inventory: {image_id!r}")
                raise SystemExit(1)
            images.add(image_id)

out_path.write_text("".join(f"{image}\n" for image in sorted(images)), encoding="utf-8")
PY
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
