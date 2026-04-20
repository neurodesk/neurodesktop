#!/bin/bash
# nbi_setup.sh
# Keep Notebook Intelligence's chat/inline-completion provider aligned with
# the OpenCode default (llm.neurodesk.org gpt-oss) and inject the shared
# NEURODESK_API_KEY (the one OpenCode persists to ~/.bashrc) so NBI can
# authenticate without the user re-entering the key in the Settings UI.
#
# Called from jupyterlab_startup.sh after restore_home_defaults.sh has
# dropped the default /opt/jovyan_defaults/.jupyter/nbi/config.json into
# the user's home.

set -u

NBI_CONFIG_FILE="${HOME}/.jupyter/nbi/config.json"
NBI_DEFAULT_CONFIG="/opt/jovyan_defaults/.jupyter/nbi/config.json"

mkdir -p "$(dirname "${NBI_CONFIG_FILE}")" 2>/dev/null || true

# If the user hasn't ended up with a config (e.g. restore was skipped), seed
# from the image default so NBI boots with the OpenCode-compatible provider.
if [ ! -f "${NBI_CONFIG_FILE}" ] && [ -f "${NBI_DEFAULT_CONFIG}" ]; then
    cp "${NBI_DEFAULT_CONFIG}" "${NBI_CONFIG_FILE}" 2>/dev/null || true
fi

if [ ! -f "${NBI_CONFIG_FILE}" ]; then
    exit 0
fi

# Source the NEURODESK_API_KEY from ~/.bashrc (opencode writes it there).
NEURODESK_API_KEY_VALUE="${NEURODESK_API_KEY:-}"
if [ -z "${NEURODESK_API_KEY_VALUE}" ] && [ -f "${HOME}/.bashrc" ]; then
    NEURODESK_API_KEY_VALUE=$(sed -nE \
        -e "s/^[[:space:]]*export[[:space:]]+NEURODESK_API_KEY='([^']+)'[[:space:]]*$/\1/p" \
        -e 's/^[[:space:]]*export[[:space:]]+NEURODESK_API_KEY="([^"]+)"[[:space:]]*$/\1/p' \
        -e 's/^[[:space:]]*export[[:space:]]+NEURODESK_API_KEY=([^[:space:]#]+)[[:space:]]*$/\1/p' \
        "${HOME}/.bashrc" | tail -n 1)
fi

if [ -z "${NEURODESK_API_KEY_VALUE}" ]; then
    # No key yet; NBI will boot with an empty key. The user can run opencode
    # once to set it up, or paste it into the NBI Settings dialog.
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

NBI_API_KEY="${NEURODESK_API_KEY_VALUE}" NBI_CONFIG_FILE="${NBI_CONFIG_FILE}" \
python3 - <<'PY'
import json
import os
import sys

path = os.environ["NBI_CONFIG_FILE"]
api_key = os.environ["NBI_API_KEY"]

try:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)

changed = False
for section in ("chat_model", "inline_completion_model"):
    model_cfg = cfg.get(section)
    if not isinstance(model_cfg, dict):
        continue
    if model_cfg.get("provider") != "openai-compatible":
        # Don't override a user's non-openai-compatible choice.
        continue
    props = model_cfg.get("properties")
    if not isinstance(props, list):
        continue
    # Only inject the key when the provider targets llm.neurodesk.org.
    # Jetstream and other OpenAI-compatible endpoints don't use this key.
    base_url = ""
    for prop in props:
        if isinstance(prop, dict) and prop.get("id") == "base_url":
            base_url = str(prop.get("value") or "")
            break
    if "llm.neurodesk.org" not in base_url:
        continue
    for prop in props:
        if not isinstance(prop, dict) or prop.get("id") != "api_key":
            continue
        if prop.get("value") != api_key:
            prop["value"] = api_key
            changed = True

if changed:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
PY
