"""Reproducible source pins and the actual Slurm extension build rewrite."""
import json
import re
import subprocess

from testlib import repo_path


DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")


def test_jupyterlab_slurm_source_and_builder_are_reproducibly_pinned():
    assert re.search(r'^ARG JUPYTERLAB_SLURM_REF="[0-9a-f]{40}"$', DOCKERFILE, re.M)
    assert 'git -C /tmp/jupyterlab-slurm checkout --detach "${JUPYTERLAB_SLURM_REF}"' in DOCKERFILE
    assert re.search(r'^ARG JUPYTER_BUILDER_VERSION=\d+\.\d+\.\d+$', DOCKERFILE, re.M)


def test_jupyterlab_slurm_obsolete_builder_rewrite_is_anchored():
    assert '''test "$(jq -r '.devDependencies["@jupyterlab/builder"]' /tmp/jupyterlab-slurm/package.json)" = "^4.0.0"''' in DOCKERFILE
    assert '"jupyterlab-slurm @ file:///tmp/jupyterlab-slurm"' in DOCKERFILE
    version = re.search(r'^ARG JUPYTER_BUILDER_VERSION=(\S+)$', DOCKERFILE, re.M).group(1)
    rewrite = re.search(
        r'''jq --arg version "\^\$\{JUPYTER_BUILDER_VERSION\}" \\\n\s+'([^']+)' ''',
        DOCKERFILE,
    )
    assert rewrite, "Builder migration command missing"
    original = {"name": "slurm", "devDependencies": {
        "@jupyterlab/builder": "^4.0.0", "typescript": "~5.2.0",
    }}
    result = subprocess.run(
        ["jq", "--arg", "version", "^" + version, rewrite.group(1)],
        input=json.dumps(original), capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"name": "slurm", "devDependencies": {
        "@jupyter/builder": "^" + version, "typescript": "~5.2.0",
    }}
