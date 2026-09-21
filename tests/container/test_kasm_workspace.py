"""Run in a started Kasm derivative, after its health check succeeds."""

import base64
import os
from pathlib import Path
import ssl
import subprocess
import urllib.error
import urllib.request

import pytest

from testlib import resolve_source


pytestmark = pytest.mark.skipif(
    not Path("/dockerstartup/vnc_startup.sh").is_file(),
    reason="Requires the Kasm image variant",
)


def test_desktop_and_jupyter_are_running():
    check = resolve_source(
        "/opt/neurodesktop/kasm-healthcheck.sh", "config/kasm/healthcheck.sh",
    )
    subprocess.run(["bash", str(check)], check=True)
    result = subprocess.run(
        ["xwininfo", "-root", "-tree"], check=True, capture_output=True, text=True,
    )
    assert "lxpanel" in result.stdout.lower()


def test_kasm_requires_authentication_and_accepts_session_password():
    url = "https://127.0.0.1:6901/"
    context = ssl._create_unverified_context()
    token = base64.b64encode(f"kasm_user:{os.environ['VNC_PW']}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(request, context=context, timeout=5) as response:
        assert response.status == 200
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(url, context=context, timeout=5)
    assert error.value.code == 401


def test_home_and_tool_runtime():
    assert os.getuid() == 1000
    assert Path.home() == Path("/home/kasm-user")
    assert Path("/home/jovyan").resolve() == Path.home()
    assert (Path.home() / "Desktop/JupyterLab.desktop").is_file()
    subprocess.run(["apptainer", "version"], check=True)
    subprocess.run(["bash", "-lc", "type module"], check=True)


def test_jupyter_only_listens_on_loopback():
    output = subprocess.check_output(["ss", "-ltnH"], text=True)
    listeners = [line.split()[3] for line in output.splitlines() if ":8888" in line]
    assert listeners == ["127.0.0.1:8888"]
