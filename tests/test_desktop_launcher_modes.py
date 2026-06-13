import os
import subprocess
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


def _firefox_wrapper_path():
    candidates = [
        Path("/usr/local/bin/neurodesktop-firefox"),
        REPO_ROOT / "config/firefox/neurodesktop-firefox",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    checked = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Firefox wrapper not found: {checked}")


def _fake_firefox(tmp_path):
    fake = tmp_path / "real-firefox"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\0' \"$@\" > \"$NEURODESKTOP_TEST_ARGV\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _captured_argv(path):
    raw = path.read_bytes()
    return [part.decode("utf-8") for part in raw.split(b"\0") if part]


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


def test_firefox_wrapper_uses_display_specific_profile(tmp_path):
    wrapper = _firefox_wrapper_path()
    fake = _fake_firefox(tmp_path)
    argv_file = tmp_path / "argv.bin"
    home = tmp_path / "home"
    home.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "DISPLAY": ":10.0",
            "NEURODESKTOP_REAL_FIREFOX": str(fake),
            "NEURODESKTOP_TEST_ARGV": str(argv_file),
        }
    )

    subprocess.run(
        [str(wrapper), "--new-window", "about:blank"],
        check=True,
        env=env,
    )

    profile = home / ".mozilla/neurodesktop-firefox-profiles/display-10.0"
    argv = _captured_argv(argv_file)
    assert argv == ["--profile", str(profile), "--new-window", "about:blank"]
    assert profile.is_dir()
    assert "--no-remote" not in argv


def test_firefox_wrapper_respects_explicit_profile(tmp_path):
    wrapper = _firefox_wrapper_path()
    fake = _fake_firefox(tmp_path)
    argv_file = tmp_path / "argv.bin"
    home = tmp_path / "home"
    explicit_profile = tmp_path / "custom-profile"
    home.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "DISPLAY": ":10.0",
            "NEURODESKTOP_REAL_FIREFOX": str(fake),
            "NEURODESKTOP_TEST_ARGV": str(argv_file),
        }
    )

    subprocess.run(
        [str(wrapper), "--profile", str(explicit_profile), "about:blank"],
        check=True,
        env=env,
    )

    argv = _captured_argv(argv_file)
    assert argv == ["--profile", str(explicit_profile), "about:blank"]
    assert not (home / ".mozilla/neurodesktop-firefox-profiles").exists()


def test_firefox_wrapper_passes_through_without_display(tmp_path):
    wrapper = _firefox_wrapper_path()
    fake = _fake_firefox(tmp_path)
    argv_file = tmp_path / "argv.bin"
    home = tmp_path / "home"
    home.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "NEURODESKTOP_REAL_FIREFOX": str(fake),
            "NEURODESKTOP_TEST_ARGV": str(argv_file),
        }
    )
    env.pop("DISPLAY", None)

    subprocess.run([str(wrapper), "--version"], check=True, env=env)

    assert _captured_argv(argv_file) == ["--version"]
    assert not (home / ".mozilla/neurodesktop-firefox-profiles").exists()


def test_dockerfile_installs_firefox_wrapper_and_rewrites_desktop_entry():
    dockerfile = _read_first("/opt/tests/Dockerfile", "Dockerfile")

    assert "/usr/local/bin/neurodesktop-firefox" in dockerfile
    assert "ln -sf /usr/local/bin/neurodesktop-firefox /usr/local/bin/firefox" in dockerfile
    assert "/usr/share/applications/firefox.desktop" in dockerfile
    assert "Exec=/usr/local/bin/neurodesktop-firefox" in dockerfile


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


def test_guacamole_script_uses_backend_specific_state_dirs():
    script = _read_first(
        "/opt/neurodesktop/guacamole.sh",
        "config/guacamole/guacamole.sh",
    )

    assert 'guacamole-${NEURODESKTOP_DESKTOP_BACKEND}' in script
    assert 'tomcat-${NEURODESKTOP_DESKTOP_BACKEND}' in script
    assert 'runtime-${NEURODESKTOP_DESKTOP_BACKEND}' in script
    assert 'CATALINA_BASE_PER_USER="${CATALINA_BASE:-' in script

    state_index = script.index('guacamole-${NEURODESKTOP_DESKTOP_BACKEND}')
    init_index = script.index('source /opt/neurodesktop/init_secrets.sh')
    assert state_index < init_index, (
        "backend-specific GUACAMOLE_HOME must be chosen before init_secrets.sh "
        "seeds user-mapping.xml"
    )


def test_init_secrets_uses_vnc_only_template_for_vnc_backend():
    script = _read_first(
        "/opt/neurodesktop/init_secrets.sh",
        "config/guacamole/init_secrets.sh",
    )

    assert "NEURODESKTOP_DESKTOP_BACKEND" in script
    assert 'MAPPING_TEMPLATE="/etc/guacamole/user-mapping-vnc.xml"' in script
    assert 'MAPPING_TEMPLATE="/etc/guacamole/user-mapping-vnc-rdp.xml"' in script
