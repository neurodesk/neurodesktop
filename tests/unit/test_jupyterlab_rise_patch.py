"""Build-time contract for the standalone RISE extension-isolation patch."""

import pytest

from testlib import load_source_module, repo_path


UPSTREAM_APP = '''from jupyterlab_server.config import get_page_config, recursive_update

class RiseHandler:
    def get_page_config(self):
        page_config = {}
        labextensions_path = []
        recursive_update(
            page_config,
            get_page_config(
                labextensions_path,
                logger=self.log,
            ),
        )
        return page_config
'''


def load_patcher_module():
    return load_source_module(
        "jupyterlab_rise_patch",
        "/opt/neurodesktop/patch_jupyterlab_rise.py",
        "config/jupyter/patch_jupyterlab_rise.py",
    )


def test_patch_isolates_rise_extensions_and_restores_its_cell_executor(tmp_path):
    patcher = load_patcher_module()
    package_dir = tmp_path / "jupyterlab_rise"
    package_dir.mkdir()
    app_path = package_dir / "app.py"
    app_path.write_text(UPSTREAM_APP, encoding="utf-8")

    assert patcher.patch_package(package_dir)

    patched = app_path.read_text(encoding="utf-8")
    assert patcher.PATCH_MARKER in patched
    assert '{"jupyterlab-myst", "jupyterlab-rise"}' in patched
    assert 'extension != "@jupyterlab/notebook-extension:cell-executor"' in patched
    assert not patcher.patch_package(package_dir)


def test_patch_refuses_upstream_drift(tmp_path):
    patcher = load_patcher_module()
    package_dir = tmp_path / "jupyterlab_rise"
    package_dir.mkdir()
    app_path = package_dir / "app.py"
    app_path.write_text("# upstream changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="page-config anchor"):
        patcher.patch_package(package_dir)

    assert app_path.read_text(encoding="utf-8") == "# upstream changed\n"


def test_dockerfile_applies_patch_after_installing_rise():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    package_install = dockerfile.index("jupyterlab_rise")
    patch_install = dockerfile.index("/opt/neurodesktop/patch_jupyterlab_rise.py")
    patch_run = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_jupyterlab_rise.py"
    )
    assert package_install < patch_install < patch_run
