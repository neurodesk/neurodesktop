#!/usr/bin/python3
"""Unprivileged preparation and authenticated, on-demand Jupyter for Glass."""
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def private_directory():
    parent = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    if parent.is_symlink() or parent.stat().st_uid != os.getuid():
        raise ValueError("Glass requires an owned runtime directory")
    path = parent / "neurodesktop"
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Unsafe Glass state directory")
    path.chmod(0o700)
    return path


def ensure_token(directory):
    path = directory / "jupyter.token"
    fd, temporary = tempfile.mkstemp(prefix=".jupyter-token-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_hex(32))
        try:
            os.link(temporary, path)  # Publish a complete token atomically, no overwrite.
        except FileExistsError:
            pass
    finally:
        os.unlink(temporary)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise ValueError("Unsafe Jupyter token file")
        token = stream.read(65)
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("Invalid private Jupyter token")
    return path, token


def prepare():
    directory = private_directory()
    authority = directory / "Xauthority"
    if authority.is_symlink():
        raise ValueError("Refusing an Xauthority symlink")
    subprocess.run(["/usr/bin/xauth", "-f", str(authority), "add", ":0", ".",
                    secrets.token_hex(16)], check=True)
    authority.chmod(0o600)
    # Keep a user-readable corrected cpuinfo for nested Apptainer procfs.
    source = Path("/proc/cpuinfo").read_text()
    if re.search(r"(?im)^cpu\s+MHz\s*:", source):
        path = directory / "cpuinfo"
        if path.is_symlink():
            raise ValueError("Refusing a cpuinfo symlink")
        path.write_text(source)
    desktop = Path.home() / "Desktop/jupyterlab-glass.desktop"
    desktop.parent.mkdir(exist_ok=True)
    if not desktop.exists() and not desktop.is_symlink():
        shutil.copyfile("/opt/neurodesktop/glass/jupyterlab.desktop", desktop)
        desktop.chmod(0o700)
    ensure_token(directory)


def run():
    directory = private_directory()
    path, _ = ensure_token(directory)
    env = dict(os.environ)
    env.pop("JUPYTER_TOKEN", None)
    env["JUPYTER_TOKEN_FILE"] = str(path)
    # Authentication and the generated /etc/jupyter proxy config remain active.
    port = int(env.get("NEURODESKTOP_JUPYTER_PORT", "8888"))
    if not 1024 <= port <= 65535:
        raise ValueError("Invalid Glass Jupyter port")
    os.execvpe("/bin/bash", ["bash", "-c",
               'source /opt/neurodesktop/environment_variables.sh; exec /opt/conda/bin/jupyter "$@"',
               "glass-jupyter", "lab", "--ip=0.0.0.0", f"--port={port}",
               "--ServerApp.port_retries=0", "--no-browser"], env)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_jupyter():
    directory = private_directory()
    _, token = ensure_token(directory)
    subprocess.run(["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start",
                    "neurodesktop-jupyter.service"], check=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    # CC forwards the product's fixed guest port 8888. No token in probe URLs/logs.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            port = int(os.environ.get("NEURODESKTOP_JUPYTER_PORT", "8888"))
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status",
                                         headers={"Authorization": f"token {token}"})
            with opener.open(req, timeout=2) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    else:
        raise TimeoutError("Jupyter did not become ready; see its service journal")
    env = subprocess.check_output(["/usr/bin/systemctl", "show-environment"], text=True)
    callback = next((line.split("=", 1)[1] for line in env.splitlines()
                     if line.startswith("VMSH_DESKTOP_WEBAPP_READY_URL=")), "")
    parsed = urllib.parse.urlsplit(callback)
    if parsed.scheme == "http" and parsed.hostname and not parsed.username and not parsed.password:
        with opener.open(urllib.request.Request(callback, data=b"", method="POST"), timeout=5):
            pass
        print("JupyterLab opened in your host browser.")
    else:
        print("JupyterLab is ready; open the forwarded Jupyter port in your browser.")
    # CC currently opens /lab without a token; show it only in this user's
    # interactive terminal, not in the root service journal or process argv.
    print("Paste this token at the Jupyter login page:")
    print(token)
    input("Press Enter to close this terminal. ")


if __name__ == "__main__":
    if os.geteuid() == 0 or os.environ.get("NEURODESKTOP_RUNTIME") != "glass":
        raise PermissionError("Glass Jupyter helpers require an unprivileged Glass session")
    {"prepare": prepare, "run": run, "open": open_jupyter}[sys.argv[1]]()
