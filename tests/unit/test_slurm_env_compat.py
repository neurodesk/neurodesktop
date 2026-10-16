"""Fail closed when the reviewed Slurm source or runtime no longer matches."""

import os
import subprocess

import pytest

from testlib import repo_path


def test_build_refuses_unreviewed_source_before_configuring(tmp_path):
    source = tmp_path / "source"
    (source / "src/common").mkdir(parents=True)
    env_source = source / "src/common/env.c"
    env_source.write_text("changed upstream source\n")
    configure = source / "configure"
    configure.write_text("#!/bin/sh\ntouch configured\n")
    configure.chmod(0o755)
    result = subprocess.run(
        ["bash", str(repo_path("config/slurm/build_env_compat.sh")), str(source), str(tmp_path / "out")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "FAILED" in result.stdout
    assert not (source / "configured").exists()
    assert env_source.read_text() == "changed upstream source\n"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mode,enabled", [("host", "1"), ("local", "0")])
def test_nonlocal_setup_does_not_require_the_private_worker(mode, enabled):
    result = subprocess.run(
        ["bash", str(repo_path("config/slurm/setup_and_start_slurm.sh"))],
        env={**os.environ, "NEURODESKTOP_SLURM_MODE": mode, "NEURODESKTOP_SLURM_ENABLE": enabled},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "compatibility" not in result.stderr


def test_local_setup_refuses_changed_runtime_before_starting_services(tmp_path):
    script = repo_path("config/slurm/setup_and_start_slurm.sh").read_text()
    manifest = tmp_path / "compatibility.sha256"
    binary = tmp_path / "slurmd-package"
    binary.write_text("updated package\n")
    manifest.write_text(f"{'0' * 64}  {binary}\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("slurmd", "slurmctld", "munged"):
        stub = commands / name
        stub.write_text("#!/bin/sh\nexit 99\n")
        stub.chmod(0o755)
    script = script.replace('"${EUID}"', '"0"').replace(
        "/opt/neurodesktop/slurm-env/compatibility.sha256", str(manifest),
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}",
             "NEURODESKTOP_SLURM_MODE": "local", "NEURODESKTOP_SLURM_ENABLE": "1"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "Slurm packages changed" in result.stderr


def test_private_worker_build_and_install_are_packaged():
    dockerfile = repo_path("Dockerfile").read_text()
    assert "bash /tmp/slurm-env/build_env_compat.sh /tmp/slurm-source /opt/slurm-env" in dockerfile
    assert "FROM builder AS slurm-env-builder\nARG SLURM_ENV_VERSION=" in dockerfile
    assert "COPY --from=slurm-env-builder /opt/slurm-env/ /opt/neurodesktop/slurm-env/" in dockerfile
    assert "from=slurm-env-builder,source=/usr/bin/patchelf,target=/tmp/patchelf,ro" in dockerfile
    assert "bash /tmp/slurm/install_env_compat.sh /tmp/patchelf" in dockerfile
    assert repo_path("config/slurm/user-env-25.11.2.patch").is_file()
