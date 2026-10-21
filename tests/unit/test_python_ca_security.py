"""Guard the direct trust-store security pins used by the runtime image."""

import re

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
    assert '$(/opt/conda/bin/uv --version)" = "uv ${UV_VERSION}"' in dockerfile
