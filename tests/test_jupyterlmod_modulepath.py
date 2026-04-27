import asyncio
import importlib.util
import os
import sys
import types
from pathlib import Path


def load_modulepath_module():
    repo_root = Path(__file__).resolve().parents[1]
    candidates = (
        Path("/opt/neurodesktop/jupyterlmod_modulepath.py"),
        repo_root / "config/jupyter/jupyterlmod_modulepath.py",
    )

    for candidate in candidates:
        if candidate.exists():
            spec = importlib.util.spec_from_file_location(
                "jupyterlmod_modulepath", candidate
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

    raise AssertionError("jupyterlmod_modulepath.py is not available")


def test_refresh_modulepath_adds_cvmfs_without_dropping_existing_paths(
    tmp_path, monkeypatch
):
    module = load_modulepath_module()
    local_containers = tmp_path / "containers"
    offline_modules = local_containers / "modules"
    offline_modules.mkdir(parents=True)
    cvmfs_modules = tmp_path / "cvmfs" / "neurodesk-modules"
    (cvmfs_modules / "mri").mkdir(parents=True)
    (cvmfs_modules / "workflows").mkdir()

    monkeypatch.setenv("NEURODESKTOP_LOCAL_CONTAINERS", str(local_containers))
    monkeypatch.setenv("CVMFS_MODULES", str(cvmfs_modules))
    monkeypatch.setenv("MODULEPATH", f"{offline_modules}/:/custom/modules")
    monkeypatch.setenv("CVMFS_DISABLE", "true")

    entries = module.refresh_modulepath()

    assert f"{offline_modules}/" in entries
    assert "/custom/modules" in entries
    assert str(cvmfs_modules / "mri") in entries
    assert str(cvmfs_modules / "workflows") in entries
    assert os.environ["CVMFS_DISABLE"] == "false"


def test_refresh_modulepath_preserves_local_fallback_when_cvmfs_missing(
    tmp_path, monkeypatch
):
    module = load_modulepath_module()
    local_containers = tmp_path / "containers"
    offline_modules = local_containers / "modules"
    offline_modules.mkdir(parents=True)

    monkeypatch.setenv("NEURODESKTOP_LOCAL_CONTAINERS", str(local_containers))
    monkeypatch.setenv("CVMFS_MODULES", str(tmp_path / "missing-cvmfs"))
    monkeypatch.delenv("MODULEPATH", raising=False)

    entries = module.refresh_modulepath()

    assert entries == [f"{offline_modules}/"]
    assert os.environ["MODULEPATH"] == f"{offline_modules}/"
    assert os.environ["CVMFS_DISABLE"] == "true"


def test_install_refreshes_before_jupyterlmod_api_calls(tmp_path, monkeypatch):
    module = load_modulepath_module()
    cvmfs_modules = tmp_path / "cvmfs" / "neurodesk-modules"
    (cvmfs_modules / "mri").mkdir(parents=True)

    monkeypatch.setenv("CVMFS_MODULES", str(cvmfs_modules))
    monkeypatch.delenv("MODULEPATH", raising=False)

    class FakeModuleAPI:
        async def avail(self):
            return os.environ.get("MODULEPATH", "").split(":")

    async def fake_paths_get(self):
        return os.environ.get("MODULEPATH", "")

    fake_handler = types.ModuleType("jupyterlmod.handler")
    fake_handler.MODULE = FakeModuleAPI()
    fake_handler.ModulePaths = type("ModulePaths", (), {"get": fake_paths_get})
    fake_package = types.ModuleType("jupyterlmod")
    fake_package.handler = fake_handler

    monkeypatch.setitem(sys.modules, "jupyterlmod", fake_package)
    monkeypatch.setitem(sys.modules, "jupyterlmod.handler", fake_handler)

    assert module.install() is True

    result = asyncio.run(fake_handler.MODULE.avail())
    assert str(cvmfs_modules / "mri") in result

    monkeypatch.delenv("MODULEPATH", raising=False)
    paths_result = asyncio.run(fake_handler.ModulePaths().get())
    assert str(cvmfs_modules / "mri") in paths_result
