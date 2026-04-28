from pathlib import Path


def test_default_codeserver_extensions_include_python_and_jupyter():
    """Verify the startup script installs Python and Jupyter VS Code support."""
    repo_root = Path(__file__).resolve().parents[1]
    startup_script = repo_root / "config/jupyter/jupyterlab_startup.sh"
    script = startup_script.read_text(encoding="utf-8")

    assert 'ensure_codeserver_extension "ms-python.python"' in script
    assert 'ensure_codeserver_extension "ms-toolsai.jupyter"' in script
