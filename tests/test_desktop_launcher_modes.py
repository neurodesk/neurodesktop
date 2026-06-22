import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree


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
        "original_args=(\"$@\")\n"
        "printf '%s\\0' \"$@\" >> \"${NEURODESKTOP_TEST_ALL_ARGV:-$NEURODESKTOP_TEST_ARGV.all}\"\n"
        "printf '\\n' >> \"${NEURODESKTOP_TEST_ALL_ARGV:-$NEURODESKTOP_TEST_ARGV.all}\"\n"
        "profile_spec=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"-CreateProfile\" ]; then\n"
        "    shift\n"
        "    profile_spec=\"${1:-}\"\n"
        "    break\n"
        "  fi\n"
        "  shift\n"
        "done\n"
        "if [ -n \"$profile_spec\" ]; then\n"
        "  if [ \"${NEURODESKTOP_TEST_CREATE_PROFILE_NOOP:-}\" = \"1\" ]; then\n"
        "    exit 0\n"
        "  fi\n"
        "  spec=\"$profile_spec\"\n"
        "  name=\"${spec%% *}\"\n"
        "  path=\"${spec#* }\"\n"
        "  if [ \"$path\" = \"$spec\" ]; then\n"
        "    path=\"$HOME/.mozilla/firefox/$name\"\n"
        "    stored_path=\"$name\"\n"
        "    is_relative=1\n"
        "  else\n"
        "    stored_path=\"$path\"\n"
        "    is_relative=0\n"
        "  fi\n"
        "  mkdir -p \"$path\" \"$HOME/.mozilla/firefox\"\n"
        "  if [ ! -s \"$HOME/.mozilla/firefox/profiles.ini\" ]; then\n"
        "    printf '%s\\n' '[General]' 'StartWithLastProfile=0' '' > \"$HOME/.mozilla/firefox/profiles.ini\"\n"
        "  fi\n"
        "  printf '%s\\n' '[Profile99]' \"Name=$name\" \"IsRelative=$is_relative\" \"Path=$stored_path\" >> \"$HOME/.mozilla/firefox/profiles.ini\"\n"
        "  exit 0\n"
        "fi\n"
        "printf '%s\\0' \"${original_args[@]}\" > \"$NEURODESKTOP_TEST_ARGV\"\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _captured_argv(path):
    raw = path.read_bytes()
    return [part.decode("utf-8") for part in raw.split(b"\0") if part]


def _captured_invocations(path):
    return [
        [part.decode("utf-8") for part in line.split(b"\0") if part]
        for line in path.read_bytes().splitlines()
        if line
    ]


def _rdp_param(mapping_text, name):
    root = ElementTree.fromstring(mapping_text)
    for connection in root.findall(".//connection"):
        if (connection.findtext("protocol") or "").strip() != "rdp":
            continue
        for param in connection.findall("param"):
            if param.get("name") == name:
                return (param.text or "").strip()
        raise AssertionError(f"RDP connection is missing param {name!r}")
    raise AssertionError("mapping does not contain an RDP connection")


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
    all_argv_file = tmp_path / "all-argv.bin"
    home = tmp_path / "home"
    home.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "DISPLAY": ":10.0",
            "NEURODESKTOP_REAL_FIREFOX": str(fake),
            "NEURODESKTOP_TEST_ARGV": str(argv_file),
            "NEURODESKTOP_TEST_ALL_ARGV": str(all_argv_file),
        }
    )

    subprocess.run(
        [str(wrapper), "--new-window", "about:blank"],
        check=True,
        env=env,
    )

    profile = home / ".mozilla/firefox/neurodesktop-display-10.0"
    argv = _captured_argv(argv_file)
    assert argv == [
        "-P",
        "neurodesktop-display-10.0",
        "--new-window",
        "about:blank",
    ]
    assert _captured_invocations(all_argv_file)[0] == [
        "-no-remote",
        "-CreateProfile",
        "neurodesktop-display-10.0",
    ]
    assert profile.is_dir()
    assert (
        "StartWithLastProfile=1"
        in (home / ".mozilla/firefox/profiles.ini").read_text(encoding="utf-8")
    )
    assert not (home / ".mozilla/neurodesktop-firefox-profiles").exists()


def test_firefox_wrapper_respects_profile_root_override(tmp_path):
    wrapper = _firefox_wrapper_path()
    fake = _fake_firefox(tmp_path)
    argv_file = tmp_path / "argv.bin"
    home = tmp_path / "home"
    profile_root = tmp_path / "custom-root"
    home.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "DISPLAY": ":1",
            "NEURODESKTOP_FIREFOX_PROFILE_ROOT": str(profile_root),
            "NEURODESKTOP_REAL_FIREFOX": str(fake),
            "NEURODESKTOP_TEST_ARGV": str(argv_file),
        }
    )

    subprocess.run(
        [str(wrapper), "about:blank"],
        check=True,
        env=env,
    )

    profile = profile_root / "neurodesktop-display-1"
    assert _captured_argv(argv_file) == [
        "-P",
        "neurodesktop-display-1",
        "about:blank",
    ]
    assert profile.is_dir()


def test_firefox_wrapper_uses_explicit_profile_dir_override(tmp_path):
    wrapper = _firefox_wrapper_path()
    fake = _fake_firefox(tmp_path)
    argv_file = tmp_path / "argv.bin"
    home = tmp_path / "home"
    profile_dir = tmp_path / "explicit-profile"
    home.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "DISPLAY": ":1",
            "NEURODESKTOP_FIREFOX_PROFILE_DIR": str(profile_dir),
            "NEURODESKTOP_REAL_FIREFOX": str(fake),
            "NEURODESKTOP_TEST_ARGV": str(argv_file),
        }
    )

    subprocess.run(
        [str(wrapper), "about:blank"],
        check=True,
        env=env,
    )

    argv = _captured_argv(argv_file)
    assert argv == ["-P", "neurodesktop-display-1", "about:blank"]
    assert profile_dir.is_dir()


def test_firefox_wrapper_falls_back_when_create_profile_does_not_register(tmp_path):
    wrapper = _firefox_wrapper_path()
    fake = _fake_firefox(tmp_path)
    argv_file = tmp_path / "argv.bin"
    home = tmp_path / "home"
    home.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "DISPLAY": ":1",
            "NEURODESKTOP_REAL_FIREFOX": str(fake),
            "NEURODESKTOP_TEST_ARGV": str(argv_file),
            "NEURODESKTOP_TEST_CREATE_PROFILE_NOOP": "1",
        }
    )

    subprocess.run(
        [str(wrapper), "about:blank"],
        check=True,
        env=env,
    )

    profile_dir = home / ".mozilla/firefox/neurodesktop-display-1"
    profiles_ini = home / ".mozilla/firefox/profiles.ini"
    assert _captured_argv(argv_file) == [
        "-P",
        "neurodesktop-display-1",
        "about:blank",
    ]
    assert profile_dir.is_dir()
    profiles_ini_text = profiles_ini.read_text(encoding="utf-8")
    assert "StartWithLastProfile=1" in profiles_ini_text
    assert "Name=neurodesktop-display-1" in profiles_ini_text
    assert "IsRelative=1" in profiles_ini_text
    assert "Path=neurodesktop-display-1" in profiles_ini_text
    assert "Default=1" in profiles_ini_text


def test_firefox_wrapper_fallback_enables_last_profile_for_existing_ini(tmp_path):
    wrapper = _firefox_wrapper_path()
    fake = _fake_firefox(tmp_path)
    argv_file = tmp_path / "argv.bin"
    home = tmp_path / "home"
    firefox_dir = home / ".mozilla/firefox"
    firefox_dir.mkdir(parents=True)
    (firefox_dir / "profiles.ini").write_text(
        "[General]\n"
        "StartWithLastProfile=0\n"
        "\n"
        "[Profile0]\n"
        "Name=manual\n"
        "IsRelative=1\n"
        "Path=manual\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "DISPLAY": ":1",
            "NEURODESKTOP_REAL_FIREFOX": str(fake),
            "NEURODESKTOP_TEST_ARGV": str(argv_file),
            "NEURODESKTOP_TEST_CREATE_PROFILE_NOOP": "1",
        }
    )

    subprocess.run(
        [str(wrapper), "about:blank"],
        check=True,
        env=env,
    )

    profiles_ini_text = (firefox_dir / "profiles.ini").read_text(encoding="utf-8")
    assert "StartWithLastProfile=1" in profiles_ini_text
    assert "[Profile1]" in profiles_ini_text
    assert "Name=neurodesktop-display-1" in profiles_ini_text
    assert "Default=1" in profiles_ini_text


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


def test_guacamole_rdp_template_forces_plain_rdp_security():
    active_mapping = _read_first(
        "/etc/guacamole/user-mapping-vnc-rdp.xml",
        "config/guacamole/user-mapping-vnc-rdp.xml",
    )
    assert _rdp_param(active_mapping, "security") == "rdp"

    legacy_mapping = REPO_ROOT / "config/guacamole/user-mapping.xml"
    if legacy_mapping.exists():
        assert _rdp_param(legacy_mapping.read_text(encoding="utf-8"), "security") == "rdp"


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
