#!/usr/bin/env python3
"""Keep the standalone RISE app isolated from full JupyterLab extensions.

RISE loads every federated extension installed for JupyterLab. Neurodesktop's
full application includes collaboration, chat, file-browser, and widget
extensions whose required services do not exist in RISE's smaller application.
One of those extensions also disables JupyterLab's built-in notebook cell
executor. The resulting notebook tracker never starts, and RISE renders a
blank page.

Patch the installed RISE handler to publish only the presentation extensions
Neurodesktop builds for that application. Exact anchors make a future RISE
update fail the image build instead of silently misapplying the workaround.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PATCH_MARKER = "neurodesktop-rise-extension-isolation"

PAGE_CONFIG_BEFORE = """        recursive_update(
            page_config,
            get_page_config(
                labextensions_path,
                logger=self.log,
            ),
        )
        return page_config
"""

PAGE_CONFIG_AFTER = f"""        # {PATCH_MARKER}
        rise_page_config = get_page_config(
            labextensions_path,
            logger=self.log,
        )
        rise_page_config["federated_extensions"] = [
            extension
            for extension in rise_page_config["federated_extensions"]
            if extension["name"] in {{"jupyterlab-myst", "jupyterlab-rise"}}
        ]
        rise_page_config["disabledExtensions"] = [
            extension
            for extension in rise_page_config["disabledExtensions"]
            if extension != "@jupyterlab/notebook-extension:cell-executor"
        ]
        recursive_update(page_config, rise_page_config)
        return page_config
"""


def installed_package_dir() -> Path:
    spec = importlib.util.find_spec("jupyterlab_rise")
    if spec is None or not spec.submodule_search_locations:
        raise ValueError("jupyterlab_rise is not installed")
    return Path(next(iter(spec.submodule_search_locations)))


def patch_package(package_dir: Path) -> bool:
    app_path = Path(package_dir) / "app.py"
    text = app_path.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return False
    if text.count(PAGE_CONFIG_BEFORE) != 1:
        raise ValueError(
            "RISE page-config anchor did not match exactly once; "
            "reassess the extension-isolation workaround"
        )
    app_path.write_text(
        text.replace(PAGE_CONFIG_BEFORE, PAGE_CONFIG_AFTER), encoding="utf-8"
    )
    return True


def main() -> int:
    package_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else installed_package_dir()
    try:
        changed = patch_package(package_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: failed to patch jupyterlab-rise: {exc}", file=sys.stderr)
        return 1

    state = "applied" if changed else "already present"
    print(f"jupyterlab-rise extension isolation {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
