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
NBI_MCP_FILE="${HOME}/.jupyter/nbi/mcp.json"
NBI_DEFAULT_MCP="/opt/jovyan_defaults/.jupyter/nbi/mcp.json"

mkdir -p "$(dirname "${NBI_CONFIG_FILE}")" 2>/dev/null || true

# If the user hasn't ended up with a config (e.g. restore was skipped), seed
# from the image default so NBI boots with the OpenCode-compatible provider.
if [ ! -f "${NBI_CONFIG_FILE}" ] && [ -f "${NBI_DEFAULT_CONFIG}" ]; then
    cp "${NBI_DEFAULT_CONFIG}" "${NBI_CONFIG_FILE}" 2>/dev/null || true
fi

if [ ! -f "${NBI_CONFIG_FILE}" ]; then
    exit 0
fi

# Read an exported VAR=value from ~/.bashrc (handles single, double, unquoted).
read_export_from_bashrc() {
    local var_name="$1"
    [ -f "${HOME}/.bashrc" ] || { printf ''; return; }
    sed -nE \
        -e "s/^[[:space:]]*export[[:space:]]+${var_name}='([^']+)'[[:space:]]*\$/\1/p" \
        -e "s/^[[:space:]]*export[[:space:]]+${var_name}=\"([^\"]+)\"[[:space:]]*\$/\1/p" \
        -e "s/^[[:space:]]*export[[:space:]]+${var_name}=([^[:space:]#]+)[[:space:]]*\$/\1/p" \
        "${HOME}/.bashrc" | tail -n 1
}

# Source the NEURODESK_API_KEY from ~/.bashrc (opencode writes it there).
NEURODESK_API_KEY_VALUE="${NEURODESK_API_KEY:-}"
if [ -z "${NEURODESK_API_KEY_VALUE}" ]; then
    NEURODESK_API_KEY_VALUE=$(read_export_from_bashrc NEURODESK_API_KEY)
fi

# Source BR_MCP_TOKEN the same way (opencode writes it on first run).
BR_MCP_TOKEN_VALUE="${BR_MCP_TOKEN:-}"
if [ -z "${BR_MCP_TOKEN_VALUE}" ]; then
    BR_MCP_TOKEN_VALUE=$(read_export_from_bashrc BR_MCP_TOKEN)
fi

sync_claude_md() {
    # NBI's Claude provider has its own system-prompt code path and does not
    # consult ~/.jupyter/nbi/rules/, so the neurodesk rules are ignored in
    # Claude mode. Mirror them as $HOME/CLAUDE.md so Claude Code (which NBI's
    # Claude mode drives) picks them up via its own loader.
    local marker='<!-- neurodesktop:nbi-rules (managed - do not edit) -->'
    local source_file="/opt/jovyan_defaults/.jupyter/nbi/rules/neurodesk.md"
    local target_file="${HOME}/CLAUDE.md"

    if [ ! -f "${source_file}" ]; then
        source_file="/opt/AGENTS.md"
    fi
    if [ ! -f "${source_file}" ]; then
        return 0
    fi

    if [ -e "${target_file}" ] && ! head -n 1 "${target_file}" 2>/dev/null | grep -qF "${marker}"; then
        echo "nbi_setup.sh: leaving user-authored ${target_file} untouched (no neurodesktop marker)" >&2
        return 0
    fi

    local tmp="${target_file}.tmp.$$"
    {
        printf '%s\n' "${marker}"
        cat "${source_file}"
    } > "${tmp}" 2>/dev/null || { rm -f "${tmp}"; return 0; }
    mv -f "${tmp}" "${target_file}" 2>/dev/null || rm -f "${tmp}"
}

sync_claude_md

sync_mcp_brain_researcher() {
    # Manage the brain-researcher MCP entry in ~/.jupyter/nbi/mcp.json.
    # NBI does NOT expand ${env:VAR} or ${VAR} placeholders in MCP headers
    # (mcp_manager.py:621 passes headers through verbatim), so we must inject
    # the literal BR_MCP_TOKEN value here. If the token is unset, remove any
    # existing brain-researcher entry to avoid 401s on every NBI startup.
    if ! command -v python3 >/dev/null 2>&1; then
        return 0
    fi
    mkdir -p "$(dirname "${NBI_MCP_FILE}")" 2>/dev/null || true
    if [ ! -f "${NBI_MCP_FILE}" ] && [ -f "${NBI_DEFAULT_MCP}" ]; then
        cp "${NBI_DEFAULT_MCP}" "${NBI_MCP_FILE}" 2>/dev/null || true
    fi

    NBI_MCP_FILE="${NBI_MCP_FILE}" \
    BR_MCP_TOKEN_VALUE="${BR_MCP_TOKEN_VALUE}" \
    python3 - <<'PY'
import json, os, sys
path = os.environ["NBI_MCP_FILE"]
token = os.environ.get("BR_MCP_TOKEN_VALUE", "")

if os.path.exists(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, json.JSONDecodeError):
        cfg = {}
else:
    cfg = {}
if not isinstance(cfg, dict):
    cfg = {}
servers = cfg.get("mcpServers")
if not isinstance(servers, dict):
    servers = {}
    cfg["mcpServers"] = servers

original = json.dumps(cfg, sort_keys=True)

if token:
    servers["brain-researcher"] = {
        "url": "https://brain-researcher.com/mcp",
        "headers": {"Authorization": f"Bearer {token}"},
    }
else:
    servers.pop("brain-researcher", None)

if json.dumps(cfg, sort_keys=True) == original:
    sys.exit(0)

# If we'd end up with an empty file, write an empty mcpServers object
# rather than removing the file entirely (NBI tolerates either, but keeping
# the file makes the next run idempotent).
tmp = f"{path}.tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)
PY
}

sync_mcp_brain_researcher

# Repair NBI config if the upstream Settings UI bug has wiped the
# openai-compatible endpoint settings. When the user switches the chat
# provider in the UI to e.g. Claude and back to openai-compatible, NBI's
# settings panel resets the property values (base_url / model_id) to the
# provider's blank defaults instead of merging the previously-saved values,
# and on Save persists those blanks. We detect that fingerprint here and
# restore the section from the seeded default.
if command -v python3 >/dev/null 2>&1 && [ -f "${NBI_DEFAULT_CONFIG}" ]; then
    NBI_CONFIG_FILE="${NBI_CONFIG_FILE}" NBI_DEFAULT_CONFIG="${NBI_DEFAULT_CONFIG}" \
    python3 - <<'PY'
import json
import os
import sys

cfg_path = os.environ["NBI_CONFIG_FILE"]
default_path = os.environ["NBI_DEFAULT_CONFIG"]

try:
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)

try:
    with open(default_path, "r", encoding="utf-8") as fh:
        defaults = json.load(fh)
except (OSError, json.JSONDecodeError):
    sys.exit(0)

def get_prop(props, prop_id):
    if not isinstance(props, list):
        return ""
    for prop in props:
        if isinstance(prop, dict) and prop.get("id") == prop_id:
            return str(prop.get("value") or "")
    return ""

changed = False
for section in ("chat_model", "inline_completion_model"):
    default_section = defaults.get(section)
    if not isinstance(default_section, dict):
        continue
    user_section = cfg.get(section)

    # Missing entirely -> restore from default.
    if not isinstance(user_section, dict):
        cfg[section] = json.loads(json.dumps(default_section))
        changed = True
        continue

    provider = user_section.get("provider")
    # Different provider on purpose (claude / ollama / etc.) -> hands off.
    if provider != "openai-compatible":
        continue

    base_url = get_prop(user_section.get("properties"), "base_url")
    # Custom OpenAI-compatible endpoint (e.g. Jetstream) -> hands off.
    if base_url and "llm.neurodesk.org" not in base_url:
        continue

    # provider == openai-compatible AND (base_url empty OR points at
    # llm.neurodesk.org but other fields may be wiped). Restore from default.
    default_base_url = get_prop(default_section.get("properties"), "base_url")
    default_model_id = get_prop(default_section.get("properties"), "model_id")
    user_model_id = get_prop(user_section.get("properties"), "model_id")

    if not base_url or not user_model_id:
        # Preserve the user's existing api_key value if non-empty.
        existing_api_key = get_prop(user_section.get("properties"), "api_key")
        cfg[section] = json.loads(json.dumps(default_section))
        if existing_api_key:
            for prop in cfg[section].get("properties", []):
                if isinstance(prop, dict) and prop.get("id") == "api_key":
                    prop["value"] = existing_api_key
                    break
        changed = True

if changed:
    tmp = f"{cfg_path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, cfg_path)
    print("nbi_setup.sh: repaired openai-compatible chat/inline-completion config from default", file=sys.stderr)
PY
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
