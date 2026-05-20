from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_first(*paths):
    for path in paths:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    checked = ", ".join(str(path) for path in paths)
    raise FileNotFoundError(f"None of these paths exist: {checked}")


def test_jupyter_launcher_exposes_separate_desktop_backends():
    config = _read_first(
        "/etc/jupyter/jupyter_notebook_config.py",
        "/opt/neurodesktop/jupyter_notebook_config.py.template",
        "config/jupyter/jupyter_notebook_config.py.template",
    )
    compile(config, "jupyter_notebook_config.py.template", "exec")

    assert "'neurodesktop-rdp': _neurodesktop_server('rdp', 'Neurodesktop RDP', 'neurodesktop-rdp')" in config
    assert "'neurodesktop-vnc': _neurodesktop_server('vnc', 'Neurodesktop VNC', 'neurodesktop-vnc')" in config
    assert 'NEURODESKTOP_DESKTOP_BACKEND="{backend}"' in config
    assert "launcher_enabled=False" in config


def test_guacamole_script_gates_rdp_and_vnc_startup():
    script = _read_first(
        "/opt/neurodesktop/guacamole.sh",
        "config/guacamole/guacamole.sh",
    )

    assert "NEURODESKTOP_DESKTOP_BACKEND" in script
    assert "_start_rdp=1" in script
    assert "_start_vnc=1" in script
    assert 'skipping RDP backend' in script
    assert 'skipping VNC backend' in script
    assert 'remove_mapping_connection "rdp"' in script
    assert 'remove_mapping_connection "vnc"' in script


def test_init_secrets_uses_vnc_only_template_for_vnc_backend():
    script = _read_first(
        "/opt/neurodesktop/init_secrets.sh",
        "config/guacamole/init_secrets.sh",
    )

    assert "NEURODESKTOP_DESKTOP_BACKEND" in script
    assert 'MAPPING_TEMPLATE="/etc/guacamole/user-mapping-vnc.xml"' in script
    assert 'MAPPING_TEMPLATE="/etc/guacamole/user-mapping-vnc-rdp.xml"' in script
