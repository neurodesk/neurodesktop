"""Packaging contracts for the network-backed TinyTeX installation layer."""

import re

from testlib import repo_path


def _tinytex_layer() -> str:
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    match = re.search(
        r"^RUN HOME=/root /bin/bash -lc .*?(?=^[A-Z]+ |\Z)",
        dockerfile,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "TinyTeX installation layer was removed"
    return match.group()


def test_tinytex_updates_tlmgr_before_installing_supplemental_packages():
    """A daily bundle can lag the live repository's required tlmgr revision."""
    layer = _tinytex_layer()
    self_update = 'retry "${tlmgr_path}" update --self'
    package_install = 'retry "${tlmgr_path}" install'

    assert self_update in layer, (
        "refresh tlmgr before using the live TeX repository; a repository "
        "infrastructure update otherwise makes tlmgr terminate"
    )
    assert package_install in layer, (
        "supplemental TinyTeX package downloads must tolerate transient "
        "repository failures"
    )
    assert layer.index(self_update) < layer.index(package_install)
