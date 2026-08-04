#!/usr/bin/env python3
"""Build-time patches for the installed notebook_intelligence package.

Anchored on the exact upstream code and failing the image build loudly when
the anchor no longer matches, so a notebook_intelligence version bump cannot
silently reintroduce the bug (the package is pinned in the Dockerfile for
the same reason).

Settings panel refresh (labextension JS bundle):
The upstream settings panel (settings-panel.tsx) renders from the
client-side capabilities cache fetched at page load and AUTO-SAVES its
state whenever it changes - including on mount (useEffect ->
handleSaveSettings). Merely opening "Notebook Intelligence Settings"
therefore posts stale page-load-era model settings - or empty properties
when the cached model list has no match - back to the server, which
persists them over ~/.jupyter/nbi/config.json and reverts whatever
nbi_setup.sh synced from the OpenCode model selection. The patch rewrites
the "open settings" command so it disposes any previously-built panel,
awaits a fresh capabilities fetch (the backend reloads config.json from
disk before answering), and only then builds and shows the panel: the
mount-time auto-save then writes back exactly what is on disk.

Ollama context-length fallback (server-side provider):
update_chat_model_list() takes the model family from `ollama list`, whose
`details.family` is empty for models imported from safetensors/mlx, then
looks up f"{family}.context_length" in the `ollama show` modelinfo. With an
empty family the key ".context_length" never exists, so the provider logs an
ERROR and silently drops the model from the chat model list even though the
show response carries the real context length (e.g.
"gemma4.context_length"). The patch falls back to the modelinfo's sole
"*.context_length" key; a model without any such key still raises and is
skipped exactly as before.

Re-running is safe: patched files are detected via MARKER.
"""

import argparse
import glob
import re
import sys

SETTINGS_MARKER = "neurodesk-nbi-settings-refresh"

DEFAULT_BUNDLE_GLOB = (
    "/opt/conda/share/jupyter/labextensions/@*/notebook-intelligence/static/*.js"
)

OLLAMA_MARKER = "neurodesk-nbi-ollama-context-fallback"

DEFAULT_OLLAMA_PROVIDER_GLOB = (
    "/opt/conda/lib/python3.*/site-packages/notebook_intelligence/"
    "llm_providers/ollama_llm_provider.py"
)

# The context-window lookup as shipped in notebook_intelligence 5.3.0's
# update_chat_model_list(); model_family comes from the `ollama list`
# response and is empty for safetensors/mlx imports.
OLLAMA_ANCHOR = (
    '                    context_window = model_info[f"{model_family}.context_length"]\n'
)

OLLAMA_REPLACEMENT = (
    f"                    # {OLLAMA_MARKER}: `ollama list` reports an empty\n"
    "                    # details.family for safetensors/mlx imports; fall back\n"
    "                    # to the modelinfo's *.context_length key so the model\n"
    "                    # still registers instead of being dropped.\n"
    '                    context_window = model_info.get(f"{model_family}.context_length")\n'
    "                    if context_window is None:\n"
    "                        context_window = next(\n"
    "                            (value for key, value in model_info.items()\n"
    '                             if key.endswith(".context_length")),\n'
    "                            None,\n"
    "                        )\n"
    "                    if context_window is None:\n"
    '                        raise KeyError(f"{model_family}.context_length")\n'
)

# The command registration as emitted by the current minifier:
#   label:"Notebook Intelligence Settings",execute:t=>{U.isDisposed&&(U=O()),
#   U.isAttached||e.shell.add(U,"main"),e.shell.activateById(U.id)}
COMMAND_PATTERN = re.compile(
    r'label:"Notebook Intelligence Settings",'
    r"execute:(?P<arg>\w+)=>\{"
    r"(?P<widget>\w+)\.isDisposed&&\((?P=widget)=(?P<factory>\w+)\(\)\),"
    r'(?P=widget)\.isAttached\|\|(?P<app>\w+)\.shell\.add\((?P=widget),"main"\),'
    r"(?P=app)\.shell\.activateById\((?P=widget)\.id\)\}"
)

# The NBI API class holding the static fetchCapabilities():
#   class T{static async initialize(){await this.fetchCapabilities()...
API_CLASS_PATTERN = re.compile(
    r"class (?P<api>\w+)\s*\{static async initialize\(\)\{"
    r"await this\.fetchCapabilities\(\)"
)


def patch_settings_bundle_text(text):
    """Return (patched_text, changed). Raises ValueError when the command
    anchor matches but the API class cannot be located."""
    if SETTINGS_MARKER in text:
        return text, False

    command_match = COMMAND_PATTERN.search(text)
    if command_match is None:
        return text, False

    api_match = API_CLASS_PATTERN.search(text)
    if api_match is None:
        raise ValueError(
            "found the settings command but not the NBI API class; "
            "refusing to apply a partial patch"
        )

    arg = command_match.group("arg")
    widget = command_match.group("widget")
    factory = command_match.group("factory")
    app = command_match.group("app")
    api = api_match.group("api")

    replacement = (
        'label:"Notebook Intelligence Settings",'
        f"execute:async {arg}=>{{/*{SETTINGS_MARKER}*/"
        f"{widget}.isDisposed||{widget}.dispose();"
        f"try{{await {api}.fetchCapabilities()}}catch(_){{}}"
        f'{widget}={factory}(),{app}.shell.add({widget},"main"),'
        f"{app}.shell.activateById({widget}.id)}}"
    )

    start, end = command_match.span()
    return text[:start] + replacement + text[end:], True


def apply_settings_bundle_patch(bundle_glob):
    bundle_files = sorted(glob.glob(bundle_glob))
    if not bundle_files:
        print(
            f"ERROR: no labextension bundles found under {bundle_glob}",
            file=sys.stderr,
        )
        return False

    patched = 0
    already = 0
    for bundle_file in bundle_files:
        with open(bundle_file, "r", encoding="utf-8") as fh:
            text = fh.read()
        if SETTINGS_MARKER in text:
            already += 1
            continue
        try:
            new_text, changed = patch_settings_bundle_text(text)
        except ValueError as exc:
            print(f"ERROR: {bundle_file}: {exc}", file=sys.stderr)
            return False
        if not changed:
            continue
        with open(bundle_file, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        print(f"patched settings-open refresh into {bundle_file}")
        patched += 1

    if patched == 0 and already == 0:
        print(
            "ERROR: no bundle contained the Notebook Intelligence settings "
            "command anchor. The notebook_intelligence build output changed; "
            "update patch_nbi.py (or drop the settings patch if the panel "
            "now refreshes capabilities on open).",
            file=sys.stderr,
        )
        return False

    if already and not patched:
        print(f"settings-open refresh already patched ({already} bundle(s))")
    return True


def patch_ollama_provider_text(text):
    """Return (patched_text, changed). Raises ValueError when the file is
    unpatched but the upstream anchor line cannot be found."""
    if OLLAMA_MARKER in text:
        return text, False

    if OLLAMA_ANCHOR not in text:
        raise ValueError(
            "the Ollama context_length anchor no longer matches. The "
            "notebook_intelligence provider changed; update patch_nbi.py (or "
            "drop the Ollama patch if empty-family models now register)."
        )

    return text.replace(OLLAMA_ANCHOR, OLLAMA_REPLACEMENT, 1), True


def apply_ollama_provider_patch(provider_glob):
    provider_files = sorted(glob.glob(provider_glob))
    if not provider_files:
        print(
            f"ERROR: no Ollama provider module found under {provider_glob}",
            file=sys.stderr,
        )
        return False

    for provider_file in provider_files:
        with open(provider_file, "r", encoding="utf-8") as fh:
            text = fh.read()
        try:
            new_text, changed = patch_ollama_provider_text(text)
        except ValueError as exc:
            print(f"ERROR: {provider_file}: {exc}", file=sys.stderr)
            return False
        if not changed:
            print(f"Ollama context fallback already patched in {provider_file}")
            continue
        with open(provider_file, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        print(f"patched Ollama context fallback into {provider_file}")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", metavar="GLOB", default=None)
    parser.add_argument("--ollama", metavar="GLOB", default=None)
    args = parser.parse_args(argv)

    # With explicit targets, patch only those (the unit tests exercise each
    # patch in isolation); with none, patch everything at the image defaults.
    explicit = args.bundles is not None or args.ollama is not None
    ok = True
    if args.bundles is not None or not explicit:
        ok = apply_settings_bundle_patch(args.bundles or DEFAULT_BUNDLE_GLOB) and ok
    if args.ollama is not None or not explicit:
        ok = apply_ollama_provider_patch(args.ollama or DEFAULT_OLLAMA_PROVIDER_GLOB) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
