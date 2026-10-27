"""Guard the direct Brotli security pin used by the runtime image.

The installed release is checked by the matching container test.
"""

import re

from packaging.version import Version

from testlib import repo_path


MINIMUM_FIXED_VERSION = Version("1.2.1")


def test_dockerfile_installs_brotli_at_the_security_fixed_version():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    match = re.search(
        r'^ARG BROTLI_VERSION="(\d+\.\d+\.\d+)"$',
        dockerfile,
        re.MULTILINE,
    )

    assert match is not None
    assert Version(match.group(1)) >= MINIMUM_FIXED_VERSION
    assert dockerfile.count("brotli==${BROTLI_VERSION}") == 1
    assert dockerfile.count('m.version("brotli"))\')" = "${BROTLI_VERSION}"') == 1
