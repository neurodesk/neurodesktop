#!/usr/bin/env python3

import json
import sys
from pathlib import Path

DISABLED_EXTENSIONS = {
    "@jupyterhub/jupyter-server-proxy": True,
    "@jupyterlab/apputils-extension:announcements": True,
}
SUPPORTER_OPTION = "neurodeskSupporter"


def load_page_config(page_config_path: Path) -> dict:
    if not page_config_path.exists():
        return {}

    try:
        payload = json.loads(page_config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(payload, dict):
        return {}

    return payload


def ensure_page_config(page_config_path: Path, supporter_flag_path: Path) -> dict:
    payload = load_page_config(page_config_path)

    disabled_extensions = payload.get("disabledExtensions")
    if not isinstance(disabled_extensions, dict):
        disabled_extensions = {}

    disabled_extensions.update(DISABLED_EXTENSIONS)
    payload["disabledExtensions"] = disabled_extensions
    payload[SUPPORTER_OPTION] = "true" if supporter_flag_path.is_file() else "false"

    page_config_path.parent.mkdir(parents=True, exist_ok=True)
    page_config_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return payload


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            f"Usage: {argv[0]} PAGE_CONFIG_PATH SUPPORTER_FLAG_PATH",
            file=sys.stderr,
        )
        return 2

    ensure_page_config(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
