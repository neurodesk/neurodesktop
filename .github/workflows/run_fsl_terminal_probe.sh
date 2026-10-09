#!/usr/bin/env bash
# Run the nightly FSL image-operation probe through an existing Jupyter terminal.
#
# The terminal WebSocket can close immediately after writing its final stdout
# frame.  Keep the complete stream in a file and inspect it again after both
# sides of the input pipe have stopped so that frame cannot be missed.

set -u

if [ "$#" -ne 1 ]; then
    echo "usage: JUPYTER_API_TOKEN=<token> run_fsl_terminal_probe.sh <websocket-url>" >&2
    exit 2
fi

WS_URL=$1
JUPYTER_API_TOKEN=${JUPYTER_API_TOKEN:-}
FSL_PROBE_ATTEMPTS=${FSL_PROBE_ATTEMPTS:-24}
FSL_PROBE_DELAY=${FSL_PROBE_DELAY:-5}
FSL_PROBE_HOLD_OPEN=${FSL_PROBE_HOLD_OPEN:-120}

if [ -z "$JUPYTER_API_TOKEN" ]; then
    echo "fsl-terminal-probe: JUPYTER_API_TOKEN is empty." >&2
    exit 2
fi

for value in "$FSL_PROBE_ATTEMPTS" "$FSL_PROBE_DELAY" "$FSL_PROBE_HOLD_OPEN"; do
    case "$value" in
        ''|*[!0-9]*)
            echo "fsl-terminal-probe: attempt, delay, and hold-open values must be non-negative integers." >&2
            exit 2
            ;;
    esac
done

if [ "$FSL_PROBE_ATTEMPTS" -lt 1 ]; then
    echo "fsl-terminal-probe: FSL_PROBE_ATTEMPTS must be at least 1." >&2
    exit 2
fi

FSL_RUN_MARKER="__FSLMATHS_VALID_OUTPUT__"
FSL_COMPLETE_MARKER="__FSLMATHS_COMPLETE_DONE__"
# Split both fixed markers in the remote command. The terminal echoes submitted
# input, and that echo must not be accepted as command completion or success.
CMD5="FSL_TMPDIR=\$(mktemp -d) && python -c 'import sys, numpy as np, nibabel as nib; nib.save(nib.Nifti1Image(np.ones((2, 2, 2), dtype=np.float32), np.eye(4)), sys.argv[1])' \"\$FSL_TMPDIR/input.nii.gz\" && fslmaths \"\$FSL_TMPDIR/input.nii.gz\" -mul 2 \"\$FSL_TMPDIR/output.nii.gz\" && test -s \"\$FSL_TMPDIR/output.nii.gz\"; FSL_STATUS=\$?; rm -rf \"\$FSL_TMPDIR\"; if [ \"\$FSL_STATUS\" -eq 0 ]; then echo '__FSLMATHS_VALID_'OUTPUT'__'; else echo \"__FSLMATHS_FAILED_\${FSL_STATUS}__\"; fi; echo '__FSLMATHS_COMPLETE_'DONE'__'; (exit \"\$FSL_STATUS\")"

PROBE_DIR=$(mktemp -d) || {
    echo "fsl-terminal-probe: could not create a temporary directory." >&2
    exit 1
}
WEBSOCKET_LOG="$PROBE_DIR/websocket.log"
WEBSOCKET_INPUT="$PROBE_DIR/websocket.input"
WEBSOCAT_PID=""
INPUT_PID=""

cleanup() {
    if [ -n "$WEBSOCAT_PID" ]; then
        kill "$WEBSOCAT_PID" 2>/dev/null || true
        wait "$WEBSOCAT_PID" 2>/dev/null || true
    fi
    if [ -n "$INPUT_PID" ]; then
        kill "$INPUT_PID" 2>/dev/null || true
        wait "$INPUT_PID" 2>/dev/null || true
    fi
    rm -rf "$PROBE_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

terminal_output() {
    # Decode each complete JSON frame and concatenate payloads without inserting
    # separators: markers can be split across frames. Diagnostics and an
    # unfinished final frame are not terminal output.
    jq -Rrj 'fromjson? | select(type == "array" and .[0] == "stdout") |
        .[1] | select(type == "string")' "$WEBSOCKET_LOG" 2>/dev/null || true
}

if ! mkfifo "$WEBSOCKET_INPUT"; then
    echo "fsl-terminal-probe: could not create the WebSocket input pipe." >&2
    exit 1
fi
if ! FSL_STDIN_PAYLOAD=$(jq -cn --arg data "${CMD5}"$'\r\n' '["stdin", $data]'); then
    echo "fsl-terminal-probe: could not encode the terminal command." >&2
    exit 1
fi

websocat --text "$WS_URL" \
    -H "Authorization: token $JUPYTER_API_TOKEN" \
    < "$WEBSOCKET_INPUT" > "$WEBSOCKET_LOG" 2>&1 &
WEBSOCAT_PID=$!

(
    printf '%s\n' "$FSL_STDIN_PAYLOAD"
    exec sleep "$FSL_PROBE_HOLD_OPEN"
) > "$WEBSOCKET_INPUT" &
INPUT_PID=$!

for ((attempt = 1; attempt <= FSL_PROBE_ATTEMPTS; attempt++)); do
    sleep "$FSL_PROBE_DELAY"
    echo "fsl-terminal-probe: waiting for FSL container ($((attempt * FSL_PROBE_DELAY))s elapsed)" >&2

    # If the socket ended, stop polling and inspect its now-complete log below.
    # Reading only before this check leaves a race with its final stdout frame.
    if ! kill -0 "$WEBSOCAT_PID" 2>/dev/null; then
        break
    fi

    FSL_PARTIAL=$(terminal_output)
    echo "fsl-terminal-probe: partial output: '${FSL_PARTIAL:0:100}...'" >&2
    if printf '%s\n' "$FSL_PARTIAL" | grep -Fq "$FSL_COMPLETE_MARKER"; then
        break
    fi
done

# Stop both sides of the pipe, reap them, and then take the authoritative final
# snapshot. A completion frame written as websocat exits is visible here.
kill "$WEBSOCAT_PID" 2>/dev/null || true
wait "$WEBSOCAT_PID" 2>/dev/null || true
WEBSOCAT_PID=""
kill "$INPUT_PID" 2>/dev/null || true
wait "$INPUT_PID" 2>/dev/null || true
INPUT_PID=""

FSL_OUTPUT=$(terminal_output)
printf '%s\n' "$FSL_OUTPUT"

if printf '%s\n' "$FSL_OUTPUT" | grep -Fq "$FSL_RUN_MARKER" &&
    printf '%s\n' "$FSL_OUTPUT" | grep -Fq "$FSL_COMPLETE_MARKER"; then
    exit 0
fi

if ! printf '%s\n' "$FSL_OUTPUT" | grep -Fq "$FSL_COMPLETE_MARKER"; then
    echo "fsl-terminal-probe: command did not report completion before the timeout or socket close." >&2
else
    echo "fsl-terminal-probe: FSL image operation completed unsuccessfully." >&2
fi
exit 1
