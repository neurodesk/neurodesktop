"""Guard the direct PyJWT security pin used by the runtime image.

The installed behavior is exercised by the matching container test.
"""

import re

from packaging.version import Version

from testlib import repo_path


MINIMUM_FIXED_VERSION = Version("2.14.0")


def test_dockerfile_installs_pyjwt_at_the_security_fixed_version():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    match = re.search(
        r'^ARG PYJWT_VERSION="(\d+\.\d+\.\d+)"$',
        dockerfile,
        re.MULTILINE,
    )

    assert match is not None
    assert Version(match.group(1)) >= MINIMUM_FIXED_VERSION
    assert dockerfile.count("PyJWT==${PYJWT_VERSION}") == 1
