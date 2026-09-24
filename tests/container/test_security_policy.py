"""Installed commands and Unix-socket isolation, including a second UID as root."""

import os
from pathlib import Path
import pwd
import socket
import subprocess
import tempfile
import time
from types import SimpleNamespace

import pytest

from testlib import resolve_source


def notebook_command(*arguments):
    if os.geteuid() == 0:
        return ["runuser", "-u", os.environ.get("NB_USER", "jovyan"), "--", *arguments]
    return list(arguments)


def test_restricted_sudo_rejects_shells_and_apt_overrides():
    if any(os.environ.get(name) for name in (
        "APPTAINER_CONTAINER", "SINGULARITY_CONTAINER", "APPTAINER_NAME", "SINGULARITY_NAME",
    )) or Path("/.apptainer.d").exists() or Path("/.singularity.d").exists():
        pytest.skip("Unprivileged HPC startup cannot grant container sudo permissions")
    if os.environ.get("GRANT_SUDO", "packages") != "packages":
        pytest.skip("Requires the package-only sudo profile")
    allowed = subprocess.run(notebook_command("sudo", "-n", "-l", "/usr/local/bin/apt", "install", "curl"),
                             capture_output=True)
    assert allowed.returncode == 0, allowed.stderr.decode()
    for arguments in (
        ["/bin/sh", "-c", "true"], ["/usr/bin/apt-get", "update"],
        ["/usr/local/bin/apt", "install", "-o", "APT::Update::Pre-Invoke::=/bin/sh"],
        ["/usr/local/bin/apt", "install", "/tmp/package.deb"],
    ):
        result = subprocess.run(notebook_command("sudo", "-n", *arguments), capture_output=True)
        assert result.returncode != 0, arguments


def test_code_server_private_socket_serves_owner_and_rejects_other_uid():
    config = resolve_source("/opt/neurodesktop/jupyter_notebook_config.py.template",
                            "config/jupyter/jupyter_notebook_config.py.template").read_text()
    c = SimpleNamespace(**{name: SimpleNamespace() for name in (
        "ServerProxy", "ServerApp", "FileContentsManager", "ResourceUseDisplay",
    )})
    exec(compile(config, "jupyter_notebook_config.py.template", "exec"), {"c": c})
    server = c.ServerProxy.servers["vscode"]
    assert server["unix_socket"] is True
    with tempfile.TemporaryDirectory(prefix="vscode-security-") as directory:
        root = Path(directory)
        endpoint = root / "vscode.sock"
        command = [part.replace("{unix_socket}", str(endpoint)) for part in server["command"]]
        command.extend(["--user-data-dir", str(root / "data"), "--extensions-dir", str(root / "extensions")])
        with (root / "server.log").open("w+") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 45
                while not endpoint.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.1)
                log.flush()
                log.seek(0)
                assert endpoint.exists(), log.read()
                # code-server applies --socket-mode only after listen() creates the socket.
                while endpoint.stat().st_mode & 0o777 != 0o600 and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert endpoint.stat().st_mode & 0o777 == 0o600
                assert root.stat().st_mode & 0o777 == 0o700
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(10)
                    client.connect(str(endpoint))
                    client.sendall(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                    assert b"200" in client.recv(4096).split(b"\r\n", 1)[0]
                if os.geteuid() == 0:
                    other_uid = pwd.getpwnam("nobody").pw_uid
                    probe = (
                        "import os,socket,sys; os.setgroups([]); "
                        f"os.setgid({other_uid}); os.setuid({other_uid}); "
                        "s=socket.socket(socket.AF_UNIX); "
                        "\ntry: s.connect(sys.argv[1])"
                        "\nexcept PermissionError: sys.exit(0)"
                        "\nelse: sys.exit(1)"
                    )
                    result = subprocess.run(["/usr/bin/python3", "-I", "-c", probe, str(endpoint)])
                    assert result.returncode == 0
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
