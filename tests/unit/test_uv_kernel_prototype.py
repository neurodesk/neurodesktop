"""Prototype boundaries; real uv/kernel smoke procedure is in its design record."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from testlib import repo_path


@pytest.fixture
def prototype(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "uv_prototype", repo_path("config/jupyter/uv_kernel_prototype.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(tmp_path / "jupyter"))
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000)
    return module


def test_launch_environment(prototype, monkeypatch, tmp_path):
    project = tmp_path / "analysis ü"
    venv = project / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to(sys.executable)
    prototype.atomic_json(venv / f".{prototype.MARKER}.json", {"project": str(project)})
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX"):
        monkeypatch.setenv(name, "/other-environment")
    monkeypatch.setenv("MODULEPATH", "/cvmfs/modules")
    calls = []
    monkeypatch.setattr(prototype.os, "execve", lambda *args: calls.append(args))
    prototype.launch(project, ["-f", "connection with spaces.json"])
    executable, argv, env = calls[0]
    assert executable == venv / "bin/python"
    assert argv[-2:] == ["-f", "connection with spaces.json"]
    assert env["VIRTUAL_ENV"] == str(venv)
    assert env["PATH"].split(":")[0] == str(venv / "bin")
    assert env["MODULEPATH"] == "/cvmfs/modules"
    assert not {"PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX"} & env.keys()


def test_moved_or_missing_environment(prototype, tmp_path):
    with pytest.raises(ValueError, match="rebuild"):
        prototype.launch(tmp_path, [])


def test_kernel_names_distinguish_same_basename(prototype, tmp_path):
    assert prototype.kernel_name(tmp_path / "a/analysis") != prototype.kernel_name(
        tmp_path / "b/analysis")


def test_refuses_unmanaged_venv_and_workspace(prototype, tmp_path):
    project = tmp_path / "analysis"
    (project / ".venv").mkdir(parents=True)
    with pytest.raises(ValueError, match="unmanaged"):
        prototype.prepare(project, "rebuild", Path(sys.executable))
    (tmp_path / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers = []\n')
    with pytest.raises(ValueError, match="workspaces"):
        prototype.prepare(project, "init", Path(sys.executable))


def test_remove_preserves_project(prototype, tmp_path):
    project = tmp_path / "analysis"
    project.mkdir()
    notebook = project / "analysis.ipynb"
    notebook.write_text("keep")
    spec_dir = tmp_path / "jupyter/kernels" / prototype.kernel_name(project)
    spec_dir.mkdir(parents=True)
    (spec_dir / "kernel.json").write_text(json.dumps({
        "metadata": {prototype.MARKER: {"project": str(project)}}}))
    prototype.prepare(project, "remove", Path(sys.executable))
    assert notebook.read_text() == "keep"
    assert not spec_dir.exists()


def test_init_is_repeatable_and_targets_project(prototype, monkeypatch, tmp_path):
    project = tmp_path / "analysis"
    project.mkdir()
    calls = []
    monkeypatch.setattr(prototype.shutil, "which", lambda _: "/usr/bin/uv")

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] != "/usr/bin/uv":
            return
        assert kwargs["cwd"] == project
        assert kwargs["env"]["UV_PROJECT_ENVIRONMENT"] == str(project / ".venv")
        if argv[1] == "init":
            assert argv[-2:] == ["--directory", str(project)]
            assert "--no-workspace" in argv
            (project / "pyproject.toml").write_text('[project]\nname="analysis"\nversion="0.1"\n')
        elif argv[1] == "venv":
            (project / ".venv/bin").mkdir(parents=True)
            (project / ".venv/bin/python").symlink_to(sys.executable)
            (project / ".venv/pyvenv.cfg").write_text("uv = 0.12.18\n")
        elif argv[1] == "add":
            with (project / "pyproject.toml").open("a") as stream:
                stream.write('[dependency-groups]\ndev=["ipykernel==6.31.0"]\n')
            (project / "uv.lock").write_text("locked")
        elif argv[1] == "sync":
            assert "--locked" in argv

    monkeypatch.setattr(prototype.subprocess, "run", run)
    prototype.prepare(project, "init", Path(sys.executable))
    before = (project / "pyproject.toml").read_bytes()
    calls.clear()
    prototype.prepare(project, "init", Path(sys.executable))
    assert (project / "pyproject.toml").read_bytes() == before
    assert (project / "uv.lock").read_text() == "locked"
    assert [call[1] for call in calls if call[0] == "/usr/bin/uv"] == ["sync"]
    specs = list((tmp_path / "jupyter/kernels").glob("*/kernel.json"))
    assert len(specs) == 1
    assert json.loads(specs[0].read_text())["argv"][0] == sys.executable


@pytest.fixture
def uv_boundary(prototype, monkeypatch):
    """Replace uv processes, retaining actual environment deletion and spec writes."""
    calls = []
    monkeypatch.setattr(prototype.shutil, "which", lambda _: "/usr/bin/uv")

    def run(argv, **kwargs):
        if argv[0] != "/usr/bin/uv":
            return
        calls.append(argv[1])
        project = kwargs["cwd"]
        if argv[1] == "venv":
            (project / ".venv/bin").mkdir(parents=True)
            (project / ".venv/bin/python").symlink_to(sys.executable)
            (project / ".venv/pyvenv.cfg").write_text("uv = 0.12.18\n")
        if argv[1] == "lock":
            (project / "uv.lock").write_text("locked dependencies")
        if argv[1] == "sync" and not (project / "uv.lock").is_file():
            raise prototype.subprocess.CalledProcessError(2, argv)

    monkeypatch.setattr(prototype.subprocess, "run", run)
    return calls


def project_manifest(project):
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="analysis"\nversion="0.1"\n'
        '[dependency-groups]\ndev=["ipykernel==6.31.0"]\n')


def test_init_existing_project_without_lock(prototype, uv_boundary, tmp_path):
    project = tmp_path / "analysis"
    project_manifest(project)
    before = (project / "pyproject.toml").read_bytes()
    prototype.prepare(project, "init", Path(sys.executable))
    assert (project / "uv.lock").read_text() == "locked dependencies"
    assert (project / "pyproject.toml").read_bytes() == before
    assert uv_boundary.index("lock") < uv_boundary.index("sync")


@pytest.mark.parametrize("record", ["project", "registration"])
def test_rebuild_after_uv_replaces_environment(prototype, uv_boundary, tmp_path, record):
    project = tmp_path / "analysis"
    project_manifest(project)
    (project / "uv.lock").write_text("original lock")
    (project / ".venv/bin").mkdir(parents=True)
    (project / ".venv/bin/python").symlink_to("/missing/base/python")
    (project / ".venv/pyvenv.cfg").write_text("uv = 0.12.18\n")
    (project / ".venv/old-file").write_text("replace me")
    if record == "project":
        prototype.atomic_json(project / f".{prototype.MARKER}.json", {"project": str(project)})
    else:
        spec_dir = tmp_path / "jupyter/kernels" / prototype.kernel_name(project)
        spec_dir.mkdir(parents=True)
        prototype.atomic_json(spec_dir / "kernel.json", {
            "metadata": {prototype.MARKER: {"project": str(project)}}})
    before = (project / "pyproject.toml").read_bytes()
    prototype.prepare(project, "rebuild", Path(sys.executable))
    assert (project / "pyproject.toml").read_bytes() == before
    assert (project / "uv.lock").read_text() == "original lock"
    assert not (project / ".venv/old-file").exists()
    assert (project / ".venv/bin/python").is_file()
    assert json.loads((project / f".{prototype.MARKER}.json").read_text()) == {
        "project": str(project)}


def test_remove_missing_registration_reports_failure(prototype, tmp_path, monkeypatch, capsys):
    project = tmp_path / "typo"
    monkeypatch.setattr(sys, "argv", ["uv-kernel", "remove", str(project)])
    assert prototype.main() == 1
    assert "No prototype registration" in capsys.readouterr().err


@pytest.mark.parametrize("legacy", [False, True])
def test_rebuild_moved_folder_preserves_analysis(prototype, uv_boundary, tmp_path, legacy):
    project = tmp_path / "old analysis"
    project_manifest(project)
    (project / "uv.lock").write_text("original lock")
    (project / "analysis.ipynb").write_text("notebook content")
    (project / ".venv/bin").mkdir(parents=True)
    (project / ".venv/bin/python").symlink_to(sys.executable)
    (project / ".venv/pyvenv.cfg").write_text("uv = 0.12.18\n")
    (project / ".venv/old-file").write_text("replace me")
    record_dir = project / ".venv" if legacy else project
    prototype.atomic_json(record_dir / f".{prototype.MARKER}.json", {"project": str(project)})
    before = (project / "pyproject.toml").read_bytes()
    moved = tmp_path / "new analysis ü"
    project.rename(moved)
    with pytest.raises(ValueError, match="moved"):
        prototype.prepare(moved, "init", Path(sys.executable))
    prototype.prepare(moved, "rebuild", Path(sys.executable))
    assert (moved / "pyproject.toml").read_bytes() == before
    assert (moved / "uv.lock").read_text() == "original lock"
    assert (moved / "analysis.ipynb").read_text() == "notebook content"
    assert not (moved / ".venv/old-file").exists()
    assert prototype.read_marker(moved) == {"project": str(moved)}
    assert (tmp_path / "jupyter/kernels" / prototype.kernel_name(moved) / "kernel.json").is_file()


@pytest.mark.parametrize("symlink", [False, True])
def test_owned_record_does_not_authorize_non_uv_or_linked_venv(
        prototype, uv_boundary, tmp_path, symlink):
    project = tmp_path / "analysis"
    project_manifest(project)
    (project / "uv.lock").write_text("original lock")
    prototype.atomic_json(project / f".{prototype.MARKER}.json", {"project": str(project)})
    environment = tmp_path / "external" if symlink else project / ".venv"
    environment.mkdir()
    (environment / "precious").write_text("keep")
    if symlink:
        (environment / "pyvenv.cfg").write_text("uv = 0.12.18\n")
        (project / ".venv").symlink_to(environment)
    with pytest.raises(ValueError, match="symlinked|unmanaged"):
        prototype.prepare(project, "rebuild", Path(sys.executable))
    assert (environment / "precious").read_text() == "keep"
    assert uv_boundary == []


def test_remove_incomplete_spec_is_actionable(prototype, tmp_path):
    project = tmp_path / "analysis"
    spec_dir = tmp_path / "jupyter/kernels" / prototype.kernel_name(project)
    spec_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="No prototype registration"):
        prototype.prepare(project, "remove", Path(sys.executable))
    assert spec_dir.is_dir()
