"""Check Slurm source pins and the current upstream builder requirement."""
import os
import json
import re
import shlex
import subprocess

import pytest

from testlib import repo_path


DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")


def test_jupyterlab_slurm_source_and_builder_are_reproducibly_pinned():
    assert re.search(r'^ARG JUPYTERLAB_SLURM_REF="[0-9a-f]{40}"$', DOCKERFILE, re.M)
    assert 'git -C /tmp/jupyterlab-slurm checkout --detach "${JUPYTERLAB_SLURM_REF}"' in DOCKERFILE
    assert re.search(r'^ARG JUPYTER_BUILDER_VERSION=\d+\.\d+\.\d+$', DOCKERFILE, re.M)
    assert '"jupyterlab-slurm @ file:///tmp/jupyterlab-slurm"' in DOCKERFILE
    assert 'del(.devDependencies["@jupyterlab/builder"])' not in DOCKERFILE


@pytest.mark.parametrize("builder,requested,accepted", [
    ("@jupyter/builder", "current", True),
    ("@jupyter/builder", "^0.0.0", False),
    ("@jupyterlab/builder", "^4.0.0", False),
])
def test_slurm_builder_guard_rejects_upstream_toolchain_drift(
    tmp_path, builder, requested, accepted
):
    version = re.search(r'^ARG JUPYTER_BUILDER_VERSION=(\S+)$', DOCKERFILE, re.M).group(1)
    guard = next(line.strip().removeprefix("&& ").removesuffix(" \\")
                 for line in DOCKERFILE.splitlines()
                 if "test" in line and "jq -r" in line
                 and "/tmp/jupyterlab-slurm/package.json" in line)
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"devDependencies": {
        builder: "^" + version if requested == "current" else requested,
    }}))
    result = subprocess.run(
        ["bash", "-c", guard.replace("/tmp/jupyterlab-slurm/package.json", shlex.quote(str(package)))],
        env={**os.environ, "JUPYTER_BUILDER_VERSION": version},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == (0 if accepted else 1), result.stderr
