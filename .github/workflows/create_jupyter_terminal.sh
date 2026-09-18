#!/usr/bin/env bash

# Create a terminal session on a JupyterHub single-user server, retrying the
# failures a freshly spawned server produces.
#
# The Hub reports "ready": true as soon as the user pod answers its own health
# check, but the pod still has to reach the Hub API to authorize incoming
# tokens. Until it can, it answers this POST with HTTP 500 and a "Failed to
# connect to Hub API" body, which is what broke the nightly play-eu probe in
# neurodesk/neurodesktop#932.
#
# Usage:
#   JUPYTER_API_TOKEN=<token> create_jupyter_terminal.sh <api-url> <user>
#
# Writes the created terminal's name to stdout. Diagnostics go to stderr and
# never include the token.
#
# Tunables (env): TERMINAL_CREATE_ATTEMPTS (default 8),
# TERMINAL_CREATE_DELAY (seconds between attempts, default 10).

set -uo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: JUPYTER_API_TOKEN=<token> create_jupyter_terminal.sh <api-url> <user>" >&2
    exit 2
fi

API_URL="${1%/}"
JUPYTER_USER="$2"

if [ -z "${JUPYTER_API_TOKEN:-}" ]; then
    echo "create-terminal: JUPYTER_API_TOKEN is empty; refusing to send an unauthenticated request." >&2
    exit 2
fi

attempts="${TERMINAL_CREATE_ATTEMPTS:-8}"
delay="${TERMINAL_CREATE_DELAY:-10}"
for value in "$attempts" "$delay"; do
    case "$value" in
        ''|*[!0-9]*)
            echo "create-terminal: TERMINAL_CREATE_ATTEMPTS and TERMINAL_CREATE_DELAY must be non-negative integers; got '${attempts}' and '${delay}'." >&2
            exit 2
            ;;
    esac
done
# Force decimal interpretation so values such as 08 stay valid shell integers.
attempts="$((10#$attempts))"
delay="$((10#$delay))"
if [ "$attempts" -eq 0 ]; then
    echo "create-terminal: TERMINAL_CREATE_ATTEMPTS must be at least 1." >&2
    exit 2
fi

terminals_url="$API_URL/user/$JUPYTER_USER/api/terminals"
response_body=$(mktemp) || {
    echo "create-terminal: could not create a temporary response file." >&2
    exit 1
}
trap 'rm -f "$response_body"' EXIT

attempt=1
while true; do
    status=$(curl \
        --silent --show-error --insecure \
        --connect-timeout 15 \
        --max-time 90 \
        --request POST \
        --header "Authorization: token $JUPYTER_API_TOKEN" \
        --output "$response_body" \
        --write-out '%{http_code}' \
        "$terminals_url")
    curl_exit=$?

    terminal_name=""
    case "$status" in
        200|201)
            terminal_name=$(jq -r '.name // empty' <"$response_body" 2>/dev/null) || terminal_name=""
            ;;
    esac

    if [ -n "$terminal_name" ]; then
        printf '%s\n' "$terminal_name"
        exit 0
    fi

    # Everything a settling user pod produces deserves another request: a
    # transport failure, a 5xx or throttling status, and a success status whose
    # body did not parse into a terminal name. A rejection of the request
    # itself, such as 403, repeats no matter how long we wait.
    if [ "$curl_exit" -ne 0 ]; then
        reason="curl exit ${curl_exit}"
        retryable=true
    else
        reason="HTTP ${status}"
        case "$status" in
            200|201)
                reason="HTTP ${status} without a terminal name"
                retryable=true
                ;;
            408|409|425|429|5??) retryable=true ;;
            *) retryable=false ;;
        esac
    fi

    printf 'create-terminal: attempt %d/%d failed (%s); response: %s\n' \
        "$attempt" "$attempts" "$reason" \
        "$(head -c 400 "$response_body" | tr '\r\n' '  ')" >&2

    if [ "$retryable" != true ]; then
        echo "create-terminal: ${reason} will not change on retry; giving up." >&2
        exit 1
    fi

    if [ "$attempt" -ge "$attempts" ]; then
        echo "create-terminal: no terminal after ${attempts} attempts; giving up." >&2
        exit 1
    fi

    echo "create-terminal: retrying in ${delay}s..." >&2
    sleep "$delay"
    attempt=$((attempt + 1))
done
