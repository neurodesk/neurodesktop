import concurrent.futures
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from testlib import load_source_module, repo_path


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    module = load_source_module("glass_jupyter", "/opt/neurodesktop/glass/jupyter.py",
                                "config/glass/jupyter.py")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return module


def test_token_is_private_persistent_and_safe_under_concurrent_launches(launcher):
    directory = launcher.private_directory()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: launcher.ensure_token(directory), range(16)))
    assert len({token for _, token in results}) == 1
    path, token = results[0]
    assert len(token) == 64
    assert path.stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700
    assert not list(directory.glob(".jupyter-token-*"))


@pytest.mark.parametrize("unsafe", ["symlink", "public", "malformed"])
def test_unsafe_token_is_rejected_without_modifying_it(launcher, tmp_path, unsafe):
    directory = launcher.private_directory()
    path = directory / "jupyter.token"
    original = tmp_path / "unrelated"
    original.write_text("private-user-data")
    if unsafe == "symlink":
        path.symlink_to(original)
    else:
        path.write_text("f" * 64 if unsafe == "public" else "invalid")
        path.chmod(0o644 if unsafe == "public" else 0o600)
    with pytest.raises((OSError, ValueError)):
        launcher.ensure_token(directory)
    assert original.read_text() == "private-user-data"
    if unsafe != "symlink":
        assert path.read_text() == ("f" * 64 if unsafe == "public" else "invalid")


def test_state_directory_symlink_does_not_change_target_permissions(launcher, tmp_path):
    target = tmp_path / "unrelated"
    target.mkdir(mode=0o755)
    (tmp_path / "neurodesktop").symlink_to(target)
    with pytest.raises(ValueError):
        launcher.private_directory()
    assert target.stat().st_mode & 0o777 == 0o755


def test_jupyter_exec_keeps_token_out_of_arguments_and_enforces_fixed_port(launcher, monkeypatch):
    calls = []
    monkeypatch.setenv("JUPYTER_TOKEN", "")
    monkeypatch.setattr(launcher.os, "execvpe", lambda *args: calls.append(args))
    launcher.run()
    _, args, env = calls[0]
    token = Path(env["JUPYTER_TOKEN_FILE"]).read_text()
    assert token not in " ".join(args)
    assert "JUPYTER_TOKEN" not in env
    assert "--ServerApp.port_retries=0" in args
    assert "--no-browser" in args
    monkeypatch.setenv("NEURODESKTOP_JUPYTER_PORT", "1")
    with pytest.raises(ValueError):
        launcher.run()
    assert len(calls) == 1


def test_security_glass_mode_keeps_package_sudo_without_rdp_or_password_mutation(tmp_path, monkeypatch):
    security = load_source_module("glass_security", "/opt/neurodesktop/startup_security.py",
                                 "config/jupyter/startup_security.py")
    for name in ("SUDOERS", "ROOT_STORAGE", "GLASS_STORAGE", "SSHD_RUNTIME", "NEUROCOMMAND"):
        monkeypatch.setattr(security, name, tmp_path / name)
    security.NEUROCOMMAND.mkdir()
    monkeypatch.setattr(security.os, "geteuid", lambda: 0)
    monkeypatch.setattr(security.os, "fchown", lambda *args: None)
    user = SimpleNamespace(pw_name="jovyan", pw_uid=1000, pw_gid=100,
                           pw_dir=str(tmp_path / "home"))
    monkeypatch.setattr(security.pwd, "getpwnam", lambda _: user)
    calls = []
    monkeypatch.setattr(security.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setenv("NEURODESKTOP_RUNTIME", "glass")
    monkeypatch.setenv("GRANT_SUDO", "packages")
    security.main()
    assert not any("chpasswd" in arg or "xrdp" in arg for args in calls for arg in args)
    package_rule = (security.SUDOERS / "notebook").read_text()
    service_rule = (security.SUDOERS / "neurodesktop-glass-jupyter").read_text()
    assert "NOSETENV: /usr/local/bin/apt, /usr/local/bin/apt-get" in package_rule
    assert "NOPASSWD:ALL" not in package_rule + service_rule
    assert service_rule.endswith("NOSETENV: /usr/bin/systemctl start neurodesktop-jupyter.service\n")
    monkeypatch.setenv("NEURODESKTOP_RUNTIME", "invalid")
    before = list(calls)
    with pytest.raises(ValueError):
        security.main()
    assert calls == before


@pytest.mark.parametrize("existing", ["empty", "data", "symlink"])
def test_cc_storage_mapping_preserves_existing_data(tmp_path, monkeypatch, existing):
    security = load_source_module("glass_storage", "/opt/neurodesktop/startup_security.py",
                                 "config/jupyter/startup_security.py")
    root = tmp_path / "root"
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "saved").write_text("share-bytes")
    monkeypatch.setattr(security, "ROOT_STORAGE", root)
    if existing == "symlink":
        old = tmp_path / "old"
        old.mkdir()
        (old / "data").write_text("home-bytes")
        root.symlink_to(old)
    else:
        root.mkdir()
        if existing == "data":
            (root / "data").write_text("root-bytes")
    if existing == "empty":
        security.prepare_storage(tmp_path / "home", shared)
        security.prepare_storage(tmp_path / "home", shared)
        assert root.resolve() == shared
    else:
        with pytest.raises(ValueError):
            security.prepare_storage(tmp_path / "home", shared)
        assert (root / "data").read_text() == ("home-bytes" if existing == "symlink" else "root-bytes")
    assert (shared / "saved").read_text() == "share-bytes"


def test_glass_helpers_compile_and_reject_missing_runtime_privileges(tmp_path):
    # Real compiler checks, not matching source strings; no hypervisor needed.
    for name in ("glass-clipboard", "glass-resize"):
        args = ["cc", "-Wall", "-Wextra", "-Werror", str(repo_path(f"config/glass/{name}.c")),
                "-o", str(tmp_path / name)]
        if name == "glass-clipboard":
            args.append("-lX11")
        subprocess.run(args, check=True, capture_output=True)
    runtime = load_source_module("glass_runtime", "/opt/neurodesktop/glass/runtime.py",
                                "config/glass/runtime.py")
    from unittest.mock import patch
    with patch.object(runtime.os, "geteuid", return_value=1000):
        with pytest.raises(PermissionError):
            runtime.session()


def test_glass_healthchecks_display_without_requiring_jupyter(tmp_path, monkeypatch):
    health = load_source_module("glass_health", "/opt/neurodesktop/glass/healthcheck.py",
                               "config/glass/healthcheck.py")
    endpoint = str(tmp_path / "X0")
    monkeypatch.setattr(health, "DISPLAY_SOCKET", endpoint)
    monkeypatch.setenv("NEURODESKTOP_RUNTIME", "glass")
    with pytest.raises(OSError):
        health.main()
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(endpoint)
        server.listen()
        health.main()
    calls = []
    monkeypatch.setenv("NEURODESKTOP_RUNTIME", "container")
    monkeypatch.setattr(health.os, "execv", lambda *args: calls.append(args))
    health.main()
    assert calls[0][0] == "/etc/jupyter/docker_healthcheck.py"


def test_root_glass_environment_does_not_create_user_owned_runtime_state(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("Root preparation boundary is covered in the explicit root test guest")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    result = subprocess.run(["bash", "-c",
        'source "$1"; test ! -e "$XDG_RUNTIME_DIR/neurodesktop"',
        "glass-test", str(repo_path("config/jupyter/environment_variables.sh"))],
        env={**os.environ, "HOME": str(tmp_path), "USER": "root", "NB_USER": "root",
             "NEURODESKTOP_RUNTIME": "glass", "XDG_RUNTIME_DIR": str(runtime)},
        capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_generated_token_authenticates_real_jupyter_server(launcher, tmp_path):
    import sys
    import time
    import urllib.request
    from urllib.error import HTTPError, URLError
    pytest.importorskip("jupyter_server")
    directory = launcher.private_directory()
    token_path, token = launcher.ensure_token(directory)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "JUPYTER_CONFIG_DIR": str(home / "config"),
           "JUPYTER_RUNTIME_DIR": str(home / "runtime"), "JUPYTER_TOKEN_FILE": str(token_path)}
    env.pop("JUPYTER_TOKEN", None)
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "jupyter_server",
            "--ServerApp.allow_root=True", "--ServerApp.open_browser=False",
            "--ServerApp.ip=127.0.0.1", f"--ServerApp.port={port}",
            "--ServerApp.port_retries=0", f"--ServerApp.root_dir={home}"],
            env=env, stdout=log, stderr=log)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), launcher.NoRedirect())
            url = f"http://127.0.0.1:{port}/api/status"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail("Jupyter server exited before readiness")
                try:
                    with opener.open(urllib.request.Request(url, headers={"Authorization": f"token {token}"}),
                                     timeout=2) as response:
                        assert response.status == 200
                    break
                except (HTTPError, URLError):
                    time.sleep(0.1)
            else:
                pytest.fail("Jupyter server did not become ready")
            with pytest.raises(HTTPError) as unauthenticated:
                opener.open(url, timeout=2)
            assert unauthenticated.value.code in (401, 403)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
