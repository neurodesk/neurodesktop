#!/usr/bin/env python3

import json
import os
import sys
import tempfile
from pathlib import Path

DISABLED_EXTENSIONS = {
    "@jupyterlab/apputils-extension:announcements": True,
}
# Disabled at sys_prefix during image build so shared-home siblings (such as
# SciDesktop) are not forced to inherit it via ~/.jupyter/labconfig/page_config.json.
LEGACY_USER_DISABLED_EXTENSIONS = (
    "@jupyterhub/jupyter-server-proxy",
)
SUPPORTER_OPTION = "neurodeskSupporter"


def load_page_config(page_config_path: Path) -> dict:
    try:
        raw = page_config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeDecodeError:
        return {}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}

    return payload


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o644

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def ensure_page_config(page_config_path: Path, supporter_flag_path: Path) -> dict:
    # Preserve user-managed symlinks by updating the resolved target file
    # rather than replacing the symlink itself.
    target_path = page_config_path.resolve() if page_config_path.is_symlink() else page_config_path
    payload = load_page_config(target_path)

    disabled_extensions = payload.get("disabledExtensions")
    if not isinstance(disabled_extensions, dict):
        disabled_extensions = {}

    for legacy_ext in LEGACY_USER_DISABLED_EXTENSIONS:
        if disabled_extensions.get(legacy_ext) is True:
            disabled_extensions.pop(legacy_ext, None)

    disabled_extensions.update(DISABLED_EXTENSIONS)
    payload["disabledExtensions"] = disabled_extensions
    payload[SUPPORTER_OPTION] = "true" if supporter_flag_path.is_file() else "false"

    _atomic_write_text(
        target_path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )

    return payload


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            f"Usage: {argv[0]} PAGE_CONFIG_PATH SUPPORTER_FLAG_PATH",
            file=sys.stderr,
        )
        return 2

    try:
        ensure_page_config(Path(argv[1]), Path(argv[2]))
    except OSError as exc:
        print(f"Failed to update {argv[1]}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
