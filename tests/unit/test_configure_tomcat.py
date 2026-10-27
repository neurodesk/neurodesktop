"""Behavioral coverage for the private Guacamole Tomcat listener."""

import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

from testlib import repo_path, resolve_source


HELPER = resolve_source(
    "/opt/neurodesktop/configure_tomcat.py",
    "config/guacamole/configure_tomcat.py",
)


def run_helper(path: Path, port: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(HELPER), str(path), str(port)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def server_xml(connectors: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Server port="8005"><Service name="Catalina">'
        f"{connectors}"
        '</Service></Server>\n'
    )


def test_cli_confines_the_active_http_connector_and_preserves_other_settings(tmp_path):
    path = tmp_path / "server.xml"
    path.write_text(
        server_xml(
            '<Connector maxHttpRequestHeaderSize="65536" address="0.0.0.0" '
            'protocol="HTTP/1.1" port="8080" />'
            '<Connector protocol="AJP/1.3" port="8009" />'
        ),
        encoding="utf-8",
    )
    path.chmod(0o640)

    result = run_helper(path, 49152)

    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    root = ElementTree.parse(path).getroot()
    assert root.get("port") == "-1"
    http, ajp = list(root.iter("Connector"))
    assert http.attrib == {
        "maxHttpRequestHeaderSize": "65536",
        "address": "127.0.0.1",
        "protocol": "HTTP/1.1",
        "port": "49152",
    }
    assert ajp.attrib == {"protocol": "AJP/1.3", "port": "8009"}
    assert path.stat().st_mode & 0o777 == 0o640
    assert sorted(item.name for item in tmp_path.iterdir()) == ["server.xml"]


@pytest.mark.parametrize(
    "connectors",
    [
        '<Connector protocol="AJP/1.3" port="8009" />',
        '<Connector protocol="HTTP/1.1" port="8080" />'
        '<Connector protocol="HTTP/1.1" port="8081" />',
    ],
    ids=["missing", "ambiguous"],
)
def test_cli_refuses_unexpected_connector_layout_without_rewriting(tmp_path, connectors):
    path = tmp_path / "server.xml"
    original = server_xml(connectors)
    path.write_text(original, encoding="utf-8")

    result = run_helper(path, 49152)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "exactly one active HTTP/1.1 connector" in result.stderr
    assert result.stderr.count("\n") == 1
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("port", [0, 1023, 65536])
def test_cli_refuses_non_user_ports_without_rewriting(tmp_path, port):
    path = tmp_path / "server.xml"
    original = server_xml('<Connector protocol="HTTP/1.1" port="8080" />')
    path.write_text(original, encoding="utf-8")

    result = run_helper(path, port)

    assert result.returncode == 1
    assert "port must be between 1024 and 65535" in result.stderr
    assert path.read_text(encoding="utf-8") == original


def test_image_default_and_runtime_both_install_the_loopback_policy():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    launcher = repo_path("config/guacamole/guacamole.sh").read_text(encoding="utf-8")

    assert 'address="127.0.0.1"' in dockerfile
    assert (
        "install -m 0755 /tmp/guacamole/configure_tomcat.py "
        "/opt/neurodesktop/configure_tomcat.py"
    ) in dockerfile
    assert "/usr/bin/python3 -I /opt/neurodesktop/configure_tomcat.py" in launcher
    assert os.access(HELPER, os.R_OK)
