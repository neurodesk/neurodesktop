import re
from pathlib import Path


def test_apptainer_dependency_apt_update_is_hardened():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(
        encoding="utf-8"
    )

    match = re.search(
        r"RUN ln -sf /opt/apptainer/bin/apptainer.*?"
        r"DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends "
        r"fuse-overlayfs squashfuse",
        dockerfile,
        flags=re.DOTALL,
    )

    assert match, "Could not find the Apptainer dependency install block"

    install_block = match.group(0)
    assert "apt-get update" in install_block
    assert install_block.index("apt-get update") < install_block.index(
        "DEBIAN_FRONTEND=noninteractive apt-get install"
    )
    assert "-o APT::Update::Error-Mode=any" in install_block
    assert "-o Acquire::Retries=5" in install_block
