"""Installed Glass support. Default Docker suite never starts native services."""
import os
from pathlib import Path
import subprocess

import pytest


def test_glass_support_is_installed_but_not_enabled_for_container_startup():
    directory = Path("/opt/neurodesktop/glass")
    assert directory.stat().st_mode & 0o777 == 0o755
    assert directory.stat().st_uid == 0
    for name in ("runtime.py", "jupyter.py", "xorg.conf", "jupyterlab.desktop"):
        path = directory / name
        assert path.stat().st_mode & 0o777 == 0o644, path
        assert path.stat().st_uid == 0, path
        assert path.read_bytes(), path
    for path in ("/usr/local/bin/glass-clipboard", "/usr/local/bin/glass-resize",
                 "/usr/local/bin/neurodesktop-start-jupyter", "/usr/lib/xorg/Xorg",
                 "/usr/bin/systemctl"):
        assert os.access(path, os.X_OK), path
    assert subprocess.run(["/usr/bin/systemctl", "--version"], capture_output=True,
                          text=True, check=True).stdout.startswith("systemd ")
    units = ["/etc/systemd/system/neurodesktop-glass.service",
             "/etc/systemd/system/neurodesktop-jupyter.service",
             "/etc/systemd/system/glass.target"]
    subprocess.run(["/usr/bin/systemd-analyze", "verify", *units], check=True,
                   capture_output=True)
    assert not Path("/etc/systemd/system/default.target").is_symlink() or (
        "glass.target" not in os.readlink("/etc/systemd/system/default.target"))
    assert not Path("/etc/systemd/system/multi-user.target.wants/neurodesktop-glass.service").exists()


@pytest.mark.skipif(os.environ.get("NEURODESKTOP_TEST_GLASS_VM") != "1",
                    reason="Requires an explicitly selected native CC VM candidate")
def test_native_glass_session_and_token_authenticated_jupyter():
    assert Path("/tmp/.X11-unix/X0").is_socket()
    subprocess.run(["/usr/bin/xrandr", "--query"], check=True, capture_output=True)
    token = Path("/run/user/1000/neurodesktop/jupyter.token")
    assert token.stat().st_mode & 0o777 == 0o600
    # This profile requires the caller to activate the Jupyter service first.
    import urllib.request
    from urllib.error import HTTPError
    url = "http://127.0.0.1:8888/api/status"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(HTTPError) as error:
        opener.open(url, timeout=5)
    assert error.value.code in (401, 403)
    request = urllib.request.Request(url, headers={"Authorization": "token " + token.read_text()})
    with opener.open(request, timeout=5) as response:
        assert response.status == 200
