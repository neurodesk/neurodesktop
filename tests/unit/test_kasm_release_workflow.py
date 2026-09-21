"""Execute the workflow's input handling with a fake registry response."""

import os
import subprocess

import pytest
import yaml

from testlib import repo_path


BASE = "ghcr.io/neurodesk/neurodesktop@sha256:" + "a" * 64


def run_inputs(tmp_path, base="", tag="", registry_ok=True):
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf \'{"digest":"sha256:' + 'a' * 64 + '"}\\n\'\n'
        if registry_ok else '#!/bin/sh\nexit 1\n'
    )
    docker.chmod(0o755)
    workflow = yaml.safe_load(repo_path(".github/workflows/release-kasm.yml").read_text())
    step = next(s for s in workflow["jobs"]["release"]["steps"] if s.get("id") == "inputs")
    env = {
        **os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "IMAGE_OWNER": "NeuroDesk", "RUN_NUMBER": "42",
        "BASE_IMAGE": base, "RELEASE_TAG": tag,
        "GITHUB_OUTPUT": str(tmp_path / "output"), "GITHUB_ENV": str(tmp_path / "env"),
    }
    return subprocess.run(["bash", "-e", "-c", step["run"]], cwd=repo_path("."), env=env, capture_output=True, text=True)


def test_defaults_resolve_digest_and_generate_owner_normalized_tag(tmp_path):
    result = run_inputs(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "env").read_text() == f"BASE_IMAGE={BASE}\n"
    output = (tmp_path / "output").read_text()
    assert "image=ghcr.io/neurodesk/neurodesktop-kasm:1.19.0-" in output
    assert output.count(".42\n") == 2


def test_explicit_digest_does_not_require_tag_lookup(tmp_path):
    result = run_inputs(tmp_path, BASE, "1.19.0-20260921.1", registry_ok=False)
    assert result.returncode == 0, result.stderr
    assert "tag=1.19.0-20260921.1\n" in (tmp_path / "output").read_text()


@pytest.mark.parametrize("base,tag", [("neurodesktop:local", ""), (BASE, "latest"), (BASE, "bad\ninjected=value")])
def test_invalid_inputs_do_not_write_github_environment(tmp_path, base, tag):
    assert run_inputs(tmp_path, base, tag).returncode != 0
    assert not (tmp_path / "env").exists()


def test_registry_failure_prevents_build_configuration(tmp_path):
    assert run_inputs(tmp_path, registry_ok=False).returncode != 0
    assert not (tmp_path / "output").exists()


def test_release_git_tag_becomes_container_tag(tmp_path):
    result = run_inputs(tmp_path, BASE, "kasm-1.19.0-20260921.1", registry_ok=False)
    assert result.returncode == 0, result.stderr
    assert "tag=1.19.0-20260921.1\n" in (tmp_path / "output").read_text()
