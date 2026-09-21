import json
import os
import pwd
from pathlib import Path
import subprocess
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from testlib import load_source_module, repo_path, resolve_source


@pytest.fixture
def security(tmp_path, monkeypatch):
    module = load_source_module("startup_security", "/opt/neurodesktop/startup_security.py",
                                "config/jupyter/startup_security.py")
    for name in ("SUDOERS", "STATE", "RUNTIME"):
        monkeypatch.setattr(module, name, tmp_path / name)
    monkeypatch.setattr(module, "XRDP_CONFIG", tmp_path / "xrdp.ini")
    module.XRDP_CONFIG.write_text("[Globals]\nport=3389\n[Xorg]\nport=-1\n")
    monkeypatch.setattr(module.os, "fchown", lambda *args: None)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    return module, calls


def account():
    return SimpleNamespace(pw_name="jovyan", pw_uid=1000, pw_gid=100)


def test_rdp_password_is_random_persistent_and_not_in_process_arguments(security):
    module, calls = security
    module.configure_rdp(account(), "3391")
    first = json.loads((module.STATE / "1000.json").read_text())["password"]
    module.configure_rdp(account(), "3391")
    assert len(first) == 48 and first != "password"
    assert json.loads((module.STATE / "1000.json").read_text())["password"] == first
    assert (module.RUNTIME / "1000/password").read_text() == first
    assert (module.RUNTIME / "1000/password").stat().st_mode & 0o777 == 0o600
    assert module.STATE.stat().st_mode & 0o777 == 0o700
    password_calls = [kwargs for args, kwargs in calls if args == ["/usr/sbin/chpasswd"]]
    assert [call["input"] for call in password_calls] == [f"jovyan:{first}\n"] * 2
    assert all(first not in " ".join(args) for args, _ in calls)
    assert "port=tcp://127.0.0.1:3391" in module.XRDP_CONFIG.read_text()
    assert "[Xorg]\nport=-1" in module.XRDP_CONFIG.read_text()


def test_rdp_replaces_runtime_symlink_without_overwriting_target(security, tmp_path):
    module, _ = security
    module.configure_rdp(account(), "3389")
    target = tmp_path / "unrelated"
    target.write_text("untouched")
    password = module.RUNTIME / "1000/password"
    password.unlink()
    password.symlink_to(target)
    module.configure_rdp(account(), "3389")
    assert target.read_text() == "untouched"
    assert not password.is_symlink()


def test_failed_password_update_does_not_start_rdp(security, monkeypatch):
    module, calls = security
    def fail(args, **kwargs):
        calls.append(args)
        raise OSError("chpasswd failed")
    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(OSError):
        module.configure_rdp(account(), "3389")
    assert calls == [["/usr/sbin/chpasswd"]]
    assert not (module.RUNTIME / "1000/password").exists()


def test_failed_optional_rdp_service_keeps_notebook_startup_available(security, monkeypatch, capsys):
    module, _ = security
    module.configure_rdp(account(), "3389")
    def fail_service(args, **kwargs):
        if args[:2] == ["/usr/sbin/service", "xrdp"]:
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(module.subprocess, "run", fail_service)
    module.configure_rdp(account(), "3389")
    assert not (module.RUNTIME / "1000/port").exists()
    assert "VNC desktop remain available" in capsys.readouterr().err


def test_restrictive_umask_still_allows_runtime_directory_traversal(security):
    module, _ = security
    previous = os.umask(0o077)
    try:
        module.configure_rdp(account(), "3389")
    finally:
        os.umask(previous)
    for directory in (module.RUNTIME.parent, module.RUNTIME, module.RUNTIME / "1000"):
        assert directory.stat().st_mode & 0o777 == 0o711
    assert (module.RUNTIME / "1000/password").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("provisioned", [True, False])
def test_guacamole_uses_only_provisioned_os_credential(tmp_path, provisioned):
    script = resolve_source("/opt/neurodesktop/init_secrets.sh", "config/guacamole/init_secrets.sh").read_text()
    templates = repo_path("config/guacamole")
    script = script.replace("/etc/guacamole/", str(templates) + "/")
    script = script.replace("/run/neurodesktop/rdp/", str(tmp_path / "credentials") + "/")
    credentials = tmp_path / "credentials" / str(os.getuid())
    credentials.mkdir(parents=True)
    username = pwd.getpwuid(os.getuid()).pw_name
    if provisioned:
        (credentials / "username").write_text(username)
        (credentials / "password").write_text("a" * 48)
    path = tmp_path / "init_secrets.sh"
    path.write_text(script)
    home = tmp_path / "home"
    env = {**os.environ, "HOME": str(home), "NB_USER": username,
           "GUACAMOLE_HOME": str(home / "guacamole"), "NEURODESKTOP_DESKTOP_BACKEND": "rdp"}
    for _ in range(2):
        subprocess.run(["bash", str(path)], env=env, capture_output=True, check=True)
        tree = ElementTree.parse(home / "guacamole/user-mapping.xml")
        connections = [node for node in tree.findall(".//connection") if node.findtext("protocol") == "rdp"]
        if provisioned:
            params = {node.get("name"): node.text for node in connections[0].findall("param")}
            assert params["password"] == "a" * 48
            assert params["username"] == username
        else:
            assert connections == []


def test_package_policy_removes_both_legacy_full_sudo_rules(security):
    module, _ = security
    module.SUDOERS.mkdir()
    for name in ("notebook", "added-by-start-script"):
        (module.SUDOERS / name).write_text("jovyan ALL=(ALL) NOPASSWD:ALL\n")
    module.configure_sudo(account(), "packages")
    assert not (module.SUDOERS / "added-by-start-script").exists()
    rule = (module.SUDOERS / "notebook").read_text()
    assert "NOPASSWD:ALL" not in rule
    assert "NOSETENV: /usr/local/bin/apt, /usr/local/bin/apt-get" in rule
    assert (module.SUDOERS / "notebook").stat().st_mode & 0o777 == 0o440
    module.configure_sudo(account(), "no")
    assert not (module.SUDOERS / "notebook").exists()


@pytest.mark.parametrize("existing", ["missing", "empty", "legacy", "data", "mounted"])
def test_root_storage_setup_preserves_data_and_home_persistence(security, tmp_path, monkeypatch, existing):
    module, _ = security
    root = tmp_path / "root-storage"
    home = tmp_path / "home"
    target = home / "neurodesktop-storage"
    monkeypatch.setattr(module, "ROOT_STORAGE", root)
    if existing != "missing":
        root.mkdir()
    if existing == "legacy":
        (root / "neurodesktop-storage").symlink_to(target)
    elif existing == "data":
        (root / "data").write_text("preserve")
    elif existing == "mounted":
        monkeypatch.setattr(Path, "is_mount", lambda path: path == root)
    module.prepare_storage(home)
    module.prepare_storage(home)
    if existing in {"missing", "empty", "legacy"}:
        assert root.is_symlink() and root.readlink() == target
    else:
        assert root.is_dir() and not root.is_symlink()
        if existing == "data":
            assert (root / "data").read_text() == "preserve"


def test_image_does_not_seed_or_restore_fixed_os_password():
    dockerfile = repo_path("Dockerfile").read_text()
    startup = repo_path("config/jupyter/before_notebook.sh").read_text()
    assert "'password' 'password' | passwd" not in dockerfile
    assert '${NB_USER}:password' not in startup
    assert "startup_security.py || exit 1" in startup


@pytest.fixture
def apt():
    return load_source_module("neurodesktop_apt", "/usr/local/bin/apt",
                              "config/jupyter/neurodesktop_apt.py")


@pytest.mark.parametrize("arguments", [
    ["install", "./package.deb"], ["install", "/tmp/package.deb"],
    ["install", "-o", "APT::Update::Pre-Invoke::=/bin/sh"],
    ["install", "--allow-unauthenticated", "curl"],
    ["install", "curl-"], ["install", "?installed"],
    ["install", "curl/stable"], ["install", "curl=1.0"],
    ["install", "--"], ["install"], ["remove", "curl"],
    ["update", "-c", "/tmp/apt.conf"],
])
def test_apt_rejects_arbitrary_options_files_and_removals(apt, arguments):
    with pytest.raises(ValueError):
        apt.apt_arguments(arguments)


def test_apt_supports_repository_install_and_update(apt):
    assert apt.apt_arguments(["update"]) == ["update"]
    arguments = apt.apt_arguments(["install", "-y", "--no-install-recommends", "g++", "curl:amd64"])
    assert arguments[-2:] == ["g++", "curl:amd64"]
    assert "--no-remove" in arguments
    assert "Dpkg::Options::=--force-confold" in arguments


def test_apt_exec_discards_untrusted_environment(apt, monkeypatch):
    monkeypatch.setenv("APT_CONFIG", "/tmp/evil.conf")
    monkeypatch.setenv("DPKG_ROOT", "/tmp/evil")
    monkeypatch.setattr(apt.sys, "argv", ["apt", "install", "curl"])
    monkeypatch.setattr(apt.os, "geteuid", lambda: 0)
    monkeypatch.setattr(apt.os, "chdir", lambda path: None)
    calls = []
    monkeypatch.setattr(apt.os, "execve", lambda *args: calls.append(args))
    apt.main()
    executable, arguments, environment = calls[0]
    assert executable == "/usr/bin/apt-get"
    assert environment == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root",
                           "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"}
