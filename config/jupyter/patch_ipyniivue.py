#!/usr/bin/env python3
"""Share ipyniivue's frontend bundle and release destroyed WebGL contexts.

``ipyniivue==2.4.4`` gives anywidget a roughly 5 MB ESM file through a synced
trait. Anywidget consequently sends and imports a separate copy for every
NiiVue model. This patch moves the heavy code to JupyterLab's same-origin
static directory and leaves a small per-model bootstrap in the Python package.
The shared module exports a factory so its original module globals remain
private to each model. The factory cleanup also relinquishes the model's WebGL
context when anywidget destroys it.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


BOOTSTRAP_MARKER = "neurodesktop-ipyniivue-shared-bundle"
MODEL_CLEANUP_MARKER = "neurodesktop-ipyniivue-model-cleanup"
ASSET_PATTERN = re.compile(
    r"neurodesktop-ipyniivue\.(?P<hash>[0-9a-f]{20})\.js"
)

STATE_BEFORE = "var vA,BC;async function BB"
STATE_AFTER = "async function BB"
FACTORY_BEFORE = "function S0(A,I){"
FACTORY_AFTER = "function createWidgetDefinition(){let vA,BC;function S0(A,I){"
CLEANUP_BEFORE = "clearInterval(BC)}},async render"
CLEANUP_AFTER = (
    f"/*{MODEL_CLEANUP_MARKER}*/vA.cleanup(),"
    'vA.gl?.getExtension("WEBGL_lose_context")?.loseContext(),'
    "vA=void 0,clearInterval(BC),BC=void 0}},async render"
)
EXPORT_BEFORE = "};export{Ih as default};"
EXPORT_AFTER = "};return Ih}export{createWidgetDefinition};"


def installed_package_dir() -> Path:
    """Return the installed ipyniivue package directory."""
    import ipyniivue

    return Path(ipyniivue.__file__).parent


def installed_lab_static_dir() -> Path:
    """Return the JupyterLab application static directory."""
    return Path(sys.prefix) / "share/jupyter/lab/static"


def _replace_once(source: str, before: str, after: str, name: str) -> str:
    if source.count(before) != 1:
        raise ValueError(
            f"ipyniivue {name} anchor did not match exactly once; "
            "reassess the frontend workaround"
        )
    return source.replace(before, after)


def _bootstrap_source(asset_name: str) -> str:
    return f'''/*{BOOTSTRAP_MARKER}*/
const configElement = document.getElementById("jupyter-config-data");
if (!configElement) {{
  throw new Error("Jupyter page config is unavailable for ipyniivue");
}}
const {{baseUrl}} = JSON.parse(configElement.textContent || "{{}}");
if (!baseUrl) {{
  throw new Error("Jupyter baseUrl is unavailable for ipyniivue");
}}
const assetUrl = new URL(
  "static/lab/{asset_name}",
  new URL(baseUrl, document.baseURI),
);
const {{createWidgetDefinition}} = await import(assetUrl.href);
export default createWidgetDefinition();
'''


def patch_ipyniivue(package_dir: Path, lab_static_dir: Path) -> bool:
    """Patch an installed ipyniivue tree and return whether files changed."""
    package_dir = Path(package_dir)
    lab_static_dir = Path(lab_static_dir)
    widget_path = package_dir / "static/widget.js"
    source = widget_path.read_text(encoding="utf-8")

    if BOOTSTRAP_MARKER in source:
        assets = ASSET_PATTERN.findall(source)
        if len(assets) != 1:
            raise ValueError("patched ipyniivue bootstrap has an invalid asset anchor")
        asset_path = lab_static_dir / f"neurodesktop-ipyniivue.{assets[0]}.js"
        asset_source = asset_path.read_text(encoding="utf-8")
        asset_hash = hashlib.sha256(asset_source.encode("utf-8")).hexdigest()[:20]
        if (
            asset_hash != assets[0]
            or MODEL_CLEANUP_MARKER not in asset_source
            or "export{createWidgetDefinition};" not in asset_source
        ):
            raise ValueError("patched ipyniivue shared asset is incomplete")
        return False

    patched = _replace_once(source, STATE_BEFORE, STATE_AFTER, "state")
    patched = _replace_once(
        patched, FACTORY_BEFORE, FACTORY_AFTER, "factory start"
    )
    patched = _replace_once(patched, CLEANUP_BEFORE, CLEANUP_AFTER, "cleanup")
    patched = _replace_once(patched, EXPORT_BEFORE, EXPORT_AFTER, "export")

    asset_hash = hashlib.sha256(patched.encode("utf-8")).hexdigest()[:20]
    asset_name = f"neurodesktop-ipyniivue.{asset_hash}.js"
    lab_static_dir.mkdir(parents=True, exist_ok=True)
    (lab_static_dir / asset_name).write_text(patched, encoding="utf-8")
    widget_path.write_text(_bootstrap_source(asset_name), encoding="utf-8")
    return True


def main() -> int:
    package_dir = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else installed_package_dir()
    )
    lab_static_dir = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else installed_lab_static_dir()
    )
    try:
        changed = patch_ipyniivue(package_dir, lab_static_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch ipyniivue: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"ipyniivue shared-bundle workaround {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
