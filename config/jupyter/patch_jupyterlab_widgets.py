#!/usr/bin/env python3
"""Extend ipywidgets' bounded model and control-state waits.

``ipywidgets==8.1.9`` retries a missing frontend model for two seconds. Server
Documents sends notebook output over its collaboration WebSocket while widget
comms use the kernel WebSocket, so a complex output can legitimately arrive
more than two seconds before one of its nested models. The widget manager also
abandons its bulk control-state request after four seconds and falls back to
individual model requests that can be stranded by the late bulk response.
During manager setup, upstream walks existing renderers before installing its
manager-backed renderer factory; collaboration output created in that gap stays
at ``Loading widget...`` and never requests state. Existing output areas also
retain their placeholder factory, so future collaboration output needs its
manager attached when the output is inserted. Publish anchored waits and this
renderer setup under new content-derived asset names. Jupyter serves the
original federated extension URLs as immutable for one year.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


MODEL_RETRY_MARKER = "neurodesktop-widget-model-retry"
CONTROL_TIMEOUT_MARKER = "neurodesktop-widget-control-timeout"
RENDERER_MANAGER_ORDER_MARKER = "neurodesktop-widget-manager-factory-first"
RENDERER_OUTPUT_WATCH_MARKER = "neurodesktop-widget-output-watch"
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

RENDERER_MANAGER_ORDER_BEFORE = (
    "async function et(e,t,i,o,a){let n,d=await ee(t),"
    "s=r.widgetManagerProperty.get(d);for(let i of(s||(s=a(),"
    "X.widgets.forEach(e=>s.register(e)),"
    "X.widgetRegistered.connect((e,t)=>{s.register(t)}),"
    "r.widgetManagerProperty.set(d,s),n=d,e.disposed.connect(e=>{"
    "r.widgetManagerProperty.get(n)&&r.widgetManagerProperty.delete(n)}),"
    "t.kernelChanged.connect((e,t)=>{let{newValue:i}=t;if(i){let e=i.id,"
    "t=r.widgetManagerProperty.get(n);t&&(r.widgetManagerProperty.delete(n),"
    "r.widgetManagerProperty.set(e,t)),n=e}})),o))i.manager=s;return "
    "i.removeMimeType(D),i.addFactory({safe:!1,mimeTypes:[D],"
    "createRenderer:e=>new h(e,s)},-10),new c.DisposableDelegate(()=>{"
    "i&&i.removeMimeType(D),s.dispose()})}"
)

RENDERER_MANAGER_ORDER_AFTER = (
    "async function et(e,t,i,o,a){let n,d=await ee(t),"
    "s=r.widgetManagerProperty.get(d);s||(s=a(),"
    "X.widgets.forEach(e=>s.register(e)),"
    "X.widgetRegistered.connect((e,t)=>{s.register(t)}),"
    "r.widgetManagerProperty.set(d,s),n=d,e.disposed.connect(e=>{"
    "r.widgetManagerProperty.get(n)&&r.widgetManagerProperty.delete(n)}),"
    "t.kernelChanged.connect((e,t)=>{let{newValue:i}=t;if(i){let e=i.id,"
    "t=r.widgetManagerProperty.get(n);t&&(r.widgetManagerProperty.delete(n),"
    "r.widgetManagerProperty.set(e,t)),n=e}}));i.removeMimeType(D),"
    "i.addFactory({safe:!1,mimeTypes:[D],createRenderer:e=>new h(e,s)},-10);"
    f"/*{RENDERER_MANAGER_ORDER_MARKER}*/"
    "for(let i of o)i.manager=s;return new c.DisposableDelegate(()=>{"
    "i&&i.removeMimeType(D),s.dispose()})}"
)

RENDERER_OUTPUT_WATCH_AFTER = (
    "async function et(e,t,i,o,a){let n,d=await ee(t),"
    "s=r.widgetManagerProperty.get(d);s||(s=a(),"
    "X.widgets.forEach(e=>s.register(e)),"
    "X.widgetRegistered.connect((e,t)=>{s.register(t)}),"
    "r.widgetManagerProperty.set(d,s),n=d,e.disposed.connect(e=>{"
    "r.widgetManagerProperty.get(n)&&r.widgetManagerProperty.delete(n)}),"
    "t.kernelChanged.connect((e,t)=>{let{newValue:i}=t;if(i){let e=i.id,"
    "t=r.widgetManagerProperty.get(n);t&&(r.widgetManagerProperty.delete(n),"
    "r.widgetManagerProperty.set(e,t)),n=e}}));"
    "let neurodeskAreas=[],neurodeskSeen=new WeakSet,"
    "neurodeskAttach=e=>{for(let t of e.widgets)"
    "for(let e of Array.from(t.children()))"
    "e instanceof h&&!neurodeskSeen.has(e)&&"
    "(neurodeskSeen.add(e),e.manager=s)};"
    "i.removeMimeType(D),i.addFactory({safe:!1,mimeTypes:[D],"
    "createRenderer:e=>{let t=new h(e,s);return neurodeskSeen.add(t),t}},-10);"
    f"/*{RENDERER_MANAGER_ORDER_MARKER}*/"
    f"/*{RENDERER_OUTPUT_WATCH_MARKER}*/"
    'for(let t of("cells"in e?Array.from(e.cells):e.widgets))'
    '"code"===t.model.type&&(neurodeskAreas.push(t.outputArea),'
    "t.outputArea.outputLengthChanged.connect(neurodeskAttach),"
    "neurodeskAttach(t.outputArea));"
    "for(let i of o)neurodeskSeen.has(i)||"
    "(neurodeskSeen.add(i),i.manager=s);"
    "return new c.DisposableDelegate(()=>{"
    "for(let e of neurodeskAreas)"
    "e.outputLengthChanged.disconnect(neurodeskAttach);"
    "i&&i.removeMimeType(D),s.dispose()})}"
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

    def is_active_asset(path: Path) -> bool:
        name_match = HASHED_BUNDLE_NAME.match(path.name)
        return bool(
            name_match and name_match.group("hash") in remote_text
        )

    def active_paths(marker: str) -> list[Path]:
        return [
            path
            for path, text in texts.items()
            if marker in text and is_active_asset(path)
        ]

    model_before_paths = [
        path
        for path, text in texts.items()
        if MODEL_RETRY_BEFORE in text and is_active_asset(path)
    ]
    renderer_before_paths = [
        path
        for path, text in texts.items()
        if RENDERER_MANAGER_ORDER_BEFORE in text and is_active_asset(path)
    ]
    renderer_order_paths = [
        path
        for path, text in texts.items()
        if RENDERER_MANAGER_ORDER_AFTER in text and is_active_asset(path)
    ]
    active_model_paths = active_paths(MODEL_RETRY_MARKER)
    active_renderer_paths = active_paths(RENDERER_OUTPUT_WATCH_MARKER)

    if (
        len(active_model_paths) == 1
        and CONTROL_TIMEOUT_MARKER in texts[active_model_paths[0]]
        and len(active_renderer_paths) == 1
        and CACHE_SAFE_MARKER in remote_text
    ):
        return False

    if len(active_model_paths) > 1 or len(active_renderer_paths) > 1:
        raise ValueError(
            "more than one active widget workaround bundle of a kind was found"
        )

    replacements_by_path: dict[Path, list[tuple[str, str]]] = {}

    def add_replacement(path: Path, before: str, after: str) -> None:
        replacements_by_path.setdefault(path, []).append((before, after))

    if active_model_paths:
        model_bundle = active_model_paths[0]
        if CONTROL_TIMEOUT_MARKER not in texts[model_bundle]:
            add_replacement(
                model_bundle,
                CONTROL_TIMEOUT_BEFORE,
                CONTROL_TIMEOUT_AFTER,
            )
    else:
        if len(model_before_paths) != 1:
            raise ValueError(
                "widget model retry anchor did not match exactly once; "
                "reassess the widget wait workaround"
            )
        model_bundle = model_before_paths[0]
        add_replacement(model_bundle, MODEL_RETRY_BEFORE, MODEL_RETRY_AFTER)
        add_replacement(
            model_bundle,
            CONTROL_TIMEOUT_BEFORE,
            CONTROL_TIMEOUT_AFTER,
        )

    if not active_renderer_paths:
        renderer_candidates = [
            (path, RENDERER_MANAGER_ORDER_BEFORE)
            for path in renderer_before_paths
        ] + [
            (path, RENDERER_MANAGER_ORDER_AFTER)
            for path in renderer_order_paths
        ]
        if len(renderer_candidates) != 1:
            raise ValueError(
                "widget renderer setup anchor did not match exactly once; "
                "reassess the widget wait workaround"
            )
        renderer_path, renderer_before = renderer_candidates[0]
        add_replacement(
            renderer_path,
            renderer_before,
            RENDERER_OUTPUT_WATCH_AFTER,
        )

    remote_match = HASHED_BUNDLE_NAME.match(remote_entry.name)
    if remote_match is None:
        raise ValueError("widget manager frontend assets are not content hashed")

    patched_remote_text = remote_text
    patched_bundles: list[tuple[Path, str]] = []
    for source_bundle, replacements in sorted(replacements_by_path.items()):
        source_text = texts[source_bundle]
        for before, _ in replacements:
            if source_text.count(before) != 1:
                raise ValueError(
                    "widget wait anchor did not match exactly once; "
                    "reassess the widget wait workaround"
                )

        source_match = HASHED_BUNDLE_NAME.match(source_bundle.name)
        if source_match is None:
            raise ValueError(
                "widget manager frontend assets are not content hashed"
            )
        source_hash = source_match.group("hash")
        if source_hash not in patched_remote_text:
            raise ValueError(
                "widget manager remote entry does not reference a patched bundle"
            )

        patched_text = source_text
        for before, after in replacements:
            patched_text = patched_text.replace(before, after)
        patched_hash = hashlib.sha256(
            patched_text.encode("utf-8")
        ).hexdigest()[:20]
        patched_bundle = static_dir / (
            f"{source_match.group('prefix')}.{patched_hash}.js"
        )
        patched_bundles.append((patched_bundle, patched_text))
        patched_remote_text = patched_remote_text.replace(
            source_hash, patched_hash
        )

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

    for patched_bundle, patched_text in patched_bundles:
        patched_bundle.write_text(patched_text, encoding="utf-8")
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
