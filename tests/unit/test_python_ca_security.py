import re
import subprocess

import pytest

from packaging.version import Version

from testlib import repo_path


SECURITY_FLOORS = {
    "CERTIFI_VERSION": Version("2026.10.19"),
    "UV_VERSION": Version("0.13.4"),
}


def test_dockerfile_installs_python_ca_consumers_at_fixed_versions():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    for argument, fixed_version in SECURITY_FLOORS.items():
        match = re.search(
            rf'^ARG {argument}="(\d+(?:\.\d+)+)"$',
            dockerfile,
            re.MULTILINE,
        )
        assert match is not None
        assert Version(match.group(1)) >= fixed_version

    assert dockerfile.count("certifi==${CERTIFI_VERSION}") == 1
    assert dockerfile.count("uv==${UV_VERSION}") == 1
    assert 'm.version("certifi")' in dockerfile
    assert dockerfile.count('= "${CERTIFI_VERSION}"') == 1


@pytest.mark.parametrize(
    ("banner", "exit_code", "accepted"),
    [
        ("uv 0.13.4 (x86_64-unknown-linux-gnu)", 0, True),
        ("uv 0.13.4 (a1b2c3d 2026-10-19 aarch64-unknown-linux-gnu)", 0, True),
        ("uv 0.13.3 (x86_64-unknown-linux-gnu)", 0, False),
        ("uv 0.13.40 (x86_64-unknown-linux-gnu)", 0, False),
        ("other 0.13.4 (x86_64-unknown-linux-gnu)", 0, False),
        ("uv 0.13.4 (x86_64-unknown-linux-gnu)", 1, False),
    ],
)
def test_build_uv_version_check(tmp_path, banner, exit_code, accepted):
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    check = next(
        line.strip().removeprefix("&& ").removesuffix(" \\")
        for line in dockerfile.splitlines()
        if "$(/opt/conda/bin/uv --version" in line
    )
    binary = tmp_path / "uv"
    binary.write_text(f"#!/bin/sh\nprintf '%s\\n' '{banner}'\nexit {exit_code}\n")
    binary.chmod(0o755)

    result = subprocess.run(
        ["bash", "-o", "pipefail", "-c", check.replace("/opt/conda/bin/uv", str(binary))],
        env={"UV_VERSION": "0.13.4"},
        capture_output=True,
        text=True,
    )

    assert (result.returncode == 0) is accepted, result.stderr
