#!/usr/bin/env python3
"""Harden ipywidgets' bounded frontend model and control-state restore.

``ipywidgets==8.1.9`` retries a missing frontend model for two seconds. Server
Documents sends notebook output over its collaboration WebSocket while widget
comms use the kernel WebSocket, so a complex output can legitimately arrive
more than two seconds before one of its nested models. The widget manager also
abandons its bulk control-state request after four seconds and falls back to
individual model requests that can be stranded by the late bulk response.
Finally, a control comm opened while a second client's kernel connection is
settling can be lost before it reaches ipykernel. Wait for that connection and
probe the kernel before opening the comm, then retry the bulk request up to twice
before falling back. Publish the anchored changes under new content-derived
asset names because Jupyter serves the original federated extension URLs as
immutable for one year.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


MODEL_RETRY_MARKER = "neurodesktop-widget-model-retry"
CONTROL_TIMEOUT_V1_MARKER = "neurodesktop-widget-control-timeout"
CONTROL_TIMEOUT_MARKER = "neurodesktop-widget-control-timeout-staged-retry"
CONTROL_RETRY_V1_MARKER = "neurodesktop-widget-control-retry"
CONTROL_RETRY_MARKER = "neurodesktop-widget-control-retry-v2"
CONNECTION_WAIT_MARKER = "neurodesktop-widget-kernel-connection-wait"
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

CONTROL_TIMEOUT_V1_AFTER = (
    "setTimeout(()=>l("
    f'/*{CONTROL_TIMEOUT_V1_MARKER}*/'
    '"Control comm did not respond in time"),3e4)'
)

CONTROL_TIMEOUT_AFTER = (
    "setTimeout(()=>l("
    f'/*{CONTROL_TIMEOUT_MARKER}*/'
    '"Control comm did not respond in time"),'
    "this.__neurodesktopControlRetry?3e4:1e4)"
)

CONTROL_RETRY_BEFORE = (
    "}catch(e){return this._loadFromKernelModels()}let o=e.states"
)

CONTROL_RETRY_V1_AFTER = (
    "}catch(e){if(!this.__neurodesktopControlRetry){"
    f"/*{CONTROL_RETRY_V1_MARKER}*/"
    "this.__neurodesktopControlRetry=!0;"
    "try{return await this._loadFromKernel()}"
    "finally{this.__neurodesktopControlRetry=!1}}"
    "return this._loadFromKernelModels()}let o=e.states"
)

CONTROL_RETRY_AFTER = (
    "}catch(e){let neurodeskRetries="
    "this.__neurodesktopControlRetryCount||0;"
    "if(neurodeskRetries<2){"
    f"/*{CONTROL_RETRY_MARKER}*/"
    "this.__neurodesktopControlRetryCount=neurodeskRetries+1;"
    "this.__neurodesktopControlRetry=!0;"
    "try{return await this._loadFromKernel()}finally{"
    "this.__neurodesktopControlRetryCount=neurodeskRetries;"
    "this.__neurodesktopControlRetry=neurodeskRetries>0}}"
    "return this._loadFromKernelModels()}let o=e.states"
)

CONNECTION_WAIT_BEFORE = "async _loadFromKernel(){let e,t;try{"

CONNECTION_WAIT_AFTER = (
    "async _loadFromKernel(){if(!this.__neurodesktopControlRetry){"
    "let neurodeskKernel=this.kernel;"
    "if(neurodeskKernel&&neurodeskKernel.connectionStatusChanged&&"
    '"connected"!==neurodeskKernel.connectionStatus)'
    "await new Promise(e=>{"
    "let neurodeskOnStatus=(i,r)=>{if(\"connected\"===r){"
    "neurodeskKernel.connectionStatusChanged.disconnect(neurodeskOnStatus),"
    "clearTimeout(neurodeskTimer),e()}},"
    "neurodeskTimer=setTimeout(()=>{"
    "neurodeskKernel.connectionStatusChanged.disconnect(neurodeskOnStatus),"
    "e()},3e4);"
    "neurodeskKernel.connectionStatusChanged.connect(neurodeskOnStatus),"
    '"connected"===neurodeskKernel.connectionStatus&&('
    "neurodeskKernel.connectionStatusChanged.disconnect(neurodeskOnStatus),"
    "clearTimeout(neurodeskTimer),e())});"
    "if(neurodeskKernel&&neurodeskKernel.requestKernelInfo)try{"
    "await new Promise((e,t)=>{"
    "let neurodeskProbeTimer=setTimeout(()=>t(Error("
    '"Kernel connection did not answer its readiness probe")),3e3);'
    "neurodeskKernel.requestKernelInfo().then(i=>{"
    "clearTimeout(neurodeskProbeTimer),e(i)},i=>{"
    "clearTimeout(neurodeskProbeTimer),t(i)})})}catch(e){}}"
    f"/*{CONNECTION_WAIT_MARKER}*/"
    "let e,t;try{"
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

    required_markers = (
        MODEL_RETRY_MARKER,
        CONTROL_TIMEOUT_MARKER,
        CONTROL_RETRY_MARKER,
        CONNECTION_WAIT_MARKER,
    )
    if (
        len(active_marker_paths) == 1
        and all(
            marker in texts[active_marker_paths[0]]
            for marker in required_markers
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
        source_text = texts[source_bundle]
        replacements = []
        if CONTROL_TIMEOUT_MARKER not in source_text:
            replacements.append(
                (
                    CONTROL_TIMEOUT_V1_AFTER
                    if CONTROL_TIMEOUT_V1_MARKER in source_text
                    else CONTROL_TIMEOUT_BEFORE,
                    CONTROL_TIMEOUT_AFTER,
                )
            )
        if CONTROL_RETRY_MARKER not in source_text:
            replacements.append(
                (
                    CONTROL_RETRY_V1_AFTER
                    if CONTROL_RETRY_V1_MARKER in source_text
                    else CONTROL_RETRY_BEFORE,
                    CONTROL_RETRY_AFTER,
                )
            )
        if CONNECTION_WAIT_MARKER not in source_text:
            replacements.append(
                (CONNECTION_WAIT_BEFORE, CONNECTION_WAIT_AFTER)
            )
    else:
        if len(model_before_paths) != 1:
            raise ValueError(
                "widget model retry anchor did not match exactly once; "
                "reassess the widget restore workaround"
            )
        source_bundle = model_before_paths[0]
        source_text = texts[source_bundle]
        replacements = [
            (MODEL_RETRY_BEFORE, MODEL_RETRY_AFTER),
            (CONTROL_TIMEOUT_BEFORE, CONTROL_TIMEOUT_AFTER),
            (CONTROL_RETRY_BEFORE, CONTROL_RETRY_AFTER),
            (CONNECTION_WAIT_BEFORE, CONNECTION_WAIT_AFTER),
        ]

    for before, _ in replacements:
        if source_text.count(before) != 1:
            raise ValueError(
                "widget restore anchor did not match exactly once; "
                "reassess the widget restore workaround"
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
    print(f"JupyterLab widget restore workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
