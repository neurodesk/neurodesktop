"""Exercise the installed uv CLI and its image-owned kernel launcher."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager


HELPER = Path("/usr/local/bin/neurodesk-uv")


def test_installed_uv_helper_uses_image_python(tmp_path):
    assert HELPER.is_file()
    assert HELPER.stat().st_uid == 0
    assert HELPER.stat().st_mode & 0o022 == 0
    assert shutil.which("neurodesk-uv") == str(HELPER)
    decoy = tmp_path / "python3"
    decoy.write_text("#!/bin/sh\nexit 99\n")
    decoy.chmod(0o755)
    environment = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
                       PYTHONHOME="/missing-python", PYTHONPATH="/missing-modules")
    result = subprocess.run([str(HELPER), "--help"], env=environment, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: neurodesk uv [-h] {init,rebuild,remove} directory\n")


def test_installed_uv_project_kernel_and_rebuild(tmp_path, monkeypatch):
    assert os.geteuid() != 0, "Run the uv environment lifecycle as the notebook user"
    project = tmp_path / "analysis ü"
    project.mkdir()
    data_dir = tmp_path / "jupyter"
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_PYTHON", "/opt/conda/bin/python")
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.delenv("UV_OFFLINE", raising=False)

    def command(action, folder=project):
        result = subprocess.run([str(HELPER), action, str(folder)], capture_output=True,
                                text=True, timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr

    command("init")
    manifest = (project / "pyproject.toml").read_bytes()
    lock = (project / "uv.lock").read_bytes()
    command("init")
    assert (project / "pyproject.toml").read_bytes() == manifest
    assert (project / "uv.lock").read_bytes() == lock
    specs = list((data_dir / "kernels").glob("*/kernel.json"))
    assert len(specs) == 1
    spec = json.loads(specs[0].read_text())
    assert spec["argv"][:4] == ["/opt/neurodesktop/kernel_wrapper.sh",
                                "/opt/conda/bin/python3", "-I", str(HELPER)]
    assert (project / ".venv").stat().st_uid == os.geteuid()
    manager = KernelManager(kernel_name=specs[0].parent.name,
                            kernel_spec_manager=KernelSpecManager(kernel_dirs=[str(data_dir / "kernels")]))
    manager.start_kernel(cwd=str(project), env=dict(os.environ, CONDA_PREFIX="/other-env",
                                                  PYTHONPATH="/missing-modules"))
    try:
        client = manager.blocking_client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=60)
            code = (
                "import os,sys; "
                f"assert sys.prefix == {str(project / '.venv')!r}; "
                "assert 'CONDA_PREFIX' not in os.environ; "
                "assert 'PYTHONPATH' not in os.environ; "
                "assert os.environ.get('MODULEPATH'); "
                "print('installed uv kernel ready')"
            )
            reply = client.execute_interactive(code, timeout=30)
            assert reply["content"]["status"] == "ok", reply
        finally:
            client.stop_channels()
    finally:
        manager.shutdown_kernel(now=True)
    moved = tmp_path / "renamed analysis"
    project.rename(moved)
    monkeypatch.setenv("UV_OFFLINE", "1")
    command("rebuild", moved)
    assert (moved / "pyproject.toml").read_bytes() == manifest
    assert (moved / "uv.lock").read_bytes() == lock
    command("remove", project)
    command("remove", moved)
    assert not list((data_dir / "kernels").glob("*/kernel.json"))
    assert (moved / ".venv/bin/python").is_file()
