from pathlib import Path


def _startup_script_path():
    installed_path = Path("/opt/neurodesktop/jupyterlab_startup.sh")
    if installed_path.exists():
        return installed_path

    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "config/jupyter/jupyterlab_startup.sh"


def test_default_codeserver_extensions_include_expected_tools():
    """Verify the startup script installs the expected default code-server tools."""
    script = _startup_script_path().read_text(encoding="utf-8")

    expected_extensions = [
        "ms-python.python",
        "ms-toolsai.jupyter",
        "ReprEng.csv",
    ]

    for extension_id in expected_extensions:
        assert f'ensure_codeserver_extension "{extension_id}"' in script
