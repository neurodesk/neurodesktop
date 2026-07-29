from testlib import resolve_source


def _startup_script_path():
    return resolve_source(
        "/opt/neurodesktop/jupyterlab_startup.sh",
        "config/jupyter/jupyterlab_startup.sh",
    )


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
