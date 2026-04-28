from pathlib import Path


def _startup_script_path():
    installed_path = Path("/opt/neurodesktop/jupyterlab_startup.sh")
    if installed_path.exists():
        return installed_path

    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "config/jupyter/jupyterlab_startup.sh"


def test_default_codeserver_extensions_include_python_and_jupyter():
    """Verify the startup script installs Python and Jupyter VS Code support."""
    script = _startup_script_path().read_text(encoding="utf-8")

    assert 'ensure_codeserver_extension "ms-python.python"' in script
    assert 'ensure_codeserver_extension "ms-toolsai.jupyter"' in script
