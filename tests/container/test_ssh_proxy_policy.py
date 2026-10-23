"""Verify the installed Jupyter SSH route obeys the startup opt-out."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("disable_ssh", ["false", "true"])
def test_installed_jupyter_ssh_proxy_route(disable_ssh, tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
from traitlets.config.loader import PyFileConfigLoader

PyFileConfigLoader('jupyter_server_config.py', path='/etc/jupyter').load_config()
from jupyter_server_proxy.config import get_entrypoint_server_processes, make_handlers

servers = get_entrypoint_server_processes()
handlers = make_handlers('/', servers)
print(json.dumps({'routes': [route[0] for route in handlers]}))
""",
        ],
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "NEURODESKTOP_DISABLE_SSH": disable_ssh,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    routes = json.loads(result.stdout.splitlines()[-1])["routes"]
    if disable_ssh == "true":
        assert "/sshd" not in routes
        assert "/sshd/(.*)" not in routes
        assert not (tmp_path / ".ssh/jupyter_sshd_hostkey").exists()
    else:
        assert "/sshd" in routes
        assert "/sshd/(.*)" in routes
        assert (tmp_path / ".ssh/jupyter_sshd_hostkey").is_file()
