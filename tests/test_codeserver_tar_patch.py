"""Ensure code-server's bundled node-tar is patched for CVE-2026-59873."""

import json
from pathlib import Path

import pytest


def _tar_version(package_json_path: Path) -> tuple[int, ...]:
    with package_json_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    version = data.get("version", "")
    try:
        return tuple(int(part) for part in version.split(".") if part.isdigit())
    except ValueError as exc:
        pytest.fail(f"Unable to parse tar version '{version}': {exc}")


@pytest.mark.parametrize(
    "tar_package_json",
    [
        Path("/opt/code-server/lib/vscode/node_modules/tar/package.json"),
        Path("/opt/code-server/node_modules/tar/package.json"),
    ],
    ids=["vscode", "codeserver-root"],
)
def test_codeserver_bundled_tar_is_patched(tar_package_json: Path) -> None:
    """CVE-2026-59873 is fixed in tar >= 7.5.19."""
    if not tar_package_json.exists():
        pytest.skip(f"{tar_package_json} not present")
    version = _tar_version(tar_package_json)
    assert version >= (7, 5, 19), (
        f"Bundled tar at {tar_package_json} is {'.'.join(str(v) for v in version)}, "
        "but >= 7.5.19 is required for CVE-2026-59873."
    )
