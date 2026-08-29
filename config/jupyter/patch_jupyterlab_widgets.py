#!/usr/bin/env python3
"""Extend ipywidgets' bounded model and control-state waits.

``ipywidgets==8.1.9`` retries a missing frontend model for two seconds. Server
Documents sends notebook output over its collaboration WebSocket while widget
comms use the kernel WebSocket, so a complex output can legitimately arrive
more than two seconds before one of its nested models. The widget manager also
abandons its bulk control-state request after four seconds and falls back to
individual model requests that can be stranded by the late bulk response.
Publish anchored ten-second model and thirty-second control-state waits under
new content-derived asset names. Jupyter serves the original federated
extension URLs as immutable for one year.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


MODEL_RETRY_MARKER = "neurodesktop-widget-model-retry"
CONTROL_TIMEOUT_MARKER = "neurodesktop-widget-control-timeout"
CACHE_SAFE_MARKER = "neurodesktop-widget-retry-cache-safe-entry"

MODEL_RETRY_BEFORE = (
    "let o=Date.now();for(;Date.now()-o<2e3;){"
    "if(void 0!==(t=this._models[e]))return t;"
    "await new Promise(e=>setTimeout(e,100))}"
)

MODEL_RETRY_AFTER = (
    f"let o=Date.now();/*{MODEL_RETRY_MARKER}*/"
    "for(;Date.now()-o<1e4;){"
    "if(void 0!==(t=this._models[e]))return t;"
    "await new Promise(e=>setTimeout(e,100))}"
)

CONTROL_TIMEOUT_BEFORE = (
    'setTimeout(()=>l("Control comm did not respond in time"),4e3)'
)

CONTROL_TIMEOUT_AFTER = (
    "setTimeout(()=>l("
    f'/*{CONTROL_TIMEOUT_MARKER}*/'
    '"Control comm did not respond in time"),3e4)'
)

HASHED_BUNDLE_NAME = re.compile(
    r"^(?P<prefix>.+)\.(?P<hash>[0-9a-f]{16,20})\.js$"
)


def installed_labextension_dir() -> Path:
    """Return the installed JupyterLab widget manager directory."""
    return (
        Path(sys.prefix)
        / "share/jupyter/labextensions/@jupyter-widgets/jupyterlab-manager"
    )


def patch_labextension(labextension_dir: Path) -> bool:
    """Patch *labextension_dir* and return whether files changed."""
    labextension_dir = Path(labextension_dir)
    static_dir = labextension_dir / "static"
    bundles = sorted(static_dir.glob("*.js"))
    if not bundles:
        raise ValueError("JupyterLab widget manager bundles were not found")

    package_path = labextension_dir / "package.json"
    package_text = package_path.read_text(encoding="utf-8")
    try:
        package_data = json.loads(package_text)
        load_path = package_data["jupyterlab"]["_build"]["load"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(
            "widget manager package.json has no valid JupyterLab build entry"
        ) from exc
    if not isinstance(load_path, str) or not load_path.startswith("static/"):
        raise ValueError("widget manager remote entry path is invalid")

    remote_entry = labextension_dir / load_path
    remote_text = remote_entry.read_text(encoding="utf-8")
    texts = {path: path.read_text(encoding="utf-8") for path in bundles}
    model_before_paths = [
        path for path, text in texts.items() if MODEL_RETRY_BEFORE in text
    ]
    active_marker_paths = []
    for path, text in texts.items():
        name_match = HASHED_BUNDLE_NAME.match(path.name)
        if (
            name_match
            and MODEL_RETRY_MARKER in text
            and name_match.group("hash") in remote_text
        ):
            active_marker_paths.append(path)

    if (
        len(active_marker_paths) == 1
        and all(
            marker in texts[active_marker_paths[0]]
            for marker in (MODEL_RETRY_MARKER, CONTROL_TIMEOUT_MARKER)
        )
        and CACHE_SAFE_MARKER in remote_text
    ):
        return False

    if len(active_marker_paths) > 1:
        raise ValueError(
            "more than one active widget workaround bundle was found"
        )

    if active_marker_paths:
        source_bundle = active_marker_paths[0]
        replacements = ((CONTROL_TIMEOUT_BEFORE, CONTROL_TIMEOUT_AFTER),)
    else:
        if len(model_before_paths) != 1:
            raise ValueError(
                "widget model retry anchor did not match exactly once; "
                "reassess the widget wait workaround"
            )
        source_bundle = model_before_paths[0]
        replacements = (
            (MODEL_RETRY_BEFORE, MODEL_RETRY_AFTER),
            (CONTROL_TIMEOUT_BEFORE, CONTROL_TIMEOUT_AFTER),
        )

    source_text = texts[source_bundle]
    for before, _ in replacements:
        if source_text.count(before) != 1:
            raise ValueError(
                "widget wait anchor did not match exactly once; "
                "reassess the widget wait workaround"
            )

    source_match = HASHED_BUNDLE_NAME.match(source_bundle.name)
    remote_match = HASHED_BUNDLE_NAME.match(remote_entry.name)
    if source_match is None or remote_match is None:
        raise ValueError("widget manager frontend assets are not content hashed")

    source_hash = source_match.group("hash")
    if source_hash not in remote_text:
        raise ValueError(
            "widget manager remote entry does not reference the model bundle"
        )

    patched_bundle_text = source_text
    for before, after in replacements:
        patched_bundle_text = patched_bundle_text.replace(before, after)
    patched_bundle_hash = hashlib.sha256(
        patched_bundle_text.encode("utf-8")
    ).hexdigest()[:20]
    patched_bundle = static_dir / (
        f"{source_match.group('prefix')}.{patched_bundle_hash}.js"
    )

    patched_remote_text = remote_text.replace(source_hash, patched_bundle_hash)
    if CACHE_SAFE_MARKER not in patched_remote_text:
        patched_remote_text += f"\n/*{CACHE_SAFE_MARKER}*/\n"
    patched_remote_hash = hashlib.sha256(
        patched_remote_text.encode("utf-8")
    ).hexdigest()[:20]
    patched_remote_entry = static_dir / (
        f"{remote_match.group('prefix')}.{patched_remote_hash}.js"
    )
    patched_load_path = f"static/{patched_remote_entry.name}"
    if package_text.count(load_path) != 1:
        raise ValueError(
            "widget manager package.json remote entry did not match exactly once"
        )

    patched_bundle.write_text(patched_bundle_text, encoding="utf-8")
    patched_remote_entry.write_text(patched_remote_text, encoding="utf-8")
    package_path.write_text(
        package_text.replace(load_path, patched_load_path), encoding="utf-8"
    )
    return True


def main() -> int:
    labextension_dir = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else installed_labextension_dir()
    )
    try:
        changed = patch_labextension(labextension_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch JupyterLab widgets: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"JupyterLab widget late-model workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
