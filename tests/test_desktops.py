import subprocess
import os
import shlex
import json
import socket
import shutil
import time
import urllib.parse
import urllib.request
import pytest

def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()


def _guacamole_ready(timeout_seconds=30):
    deadline = time.time() + timeout_seconds
    last_error = None

    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=5) as response:
                if response.status == 200:
                    return
        except Exception as error:  # pragma: no cover - exercised in retries
            last_error = error
            time.sleep(1)

    raise AssertionError(f"Guacamole UI did not become ready on port 8080: {last_error}")


def _wait_for_tcp_port(port, timeout_seconds=30):
    deadline = time.time() + timeout_seconds
    last_error = None

    while time.time() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        try:
            sock.connect(("127.0.0.1", port))
            return
        except OSError as error:  # pragma: no cover - exercised in retries
            last_error = error
            time.sleep(1)
        finally:
            sock.close()

    raise AssertionError(f"TCP port {port} did not open on 127.0.0.1: {last_error}")


def _guacamole_login(username, password):
    payload = urllib.parse.urlencode(
        {"username": username, "password": password}
    ).encode()
    request = urllib.request.Request("http://127.0.0.1:8080/api/tokens", data=payload)

    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _prepare_guacamole_runtime():
    nb_user = os.environ.get("NB_USER", "jovyan")
    nb_uid = os.environ.get("NB_UID", "1000")
    nb_gid = os.environ.get("NB_GID", "100")
    home_dir = os.path.expanduser(f"~{nb_user}")
    vnc_dir = os.path.join(home_dir, ".vnc")
    ssh_dir = os.path.join(home_dir, ".ssh")

    os.makedirs(vnc_dir, exist_ok=True)
    os.makedirs(ssh_dir, exist_ok=True)
    os.makedirs("/var/run/sshd", exist_ok=True)

    shutil.copy("/opt/jovyan_defaults/.vnc/passwd", os.path.join(vnc_dir, "passwd"))
    shutil.copy("/opt/jovyan_defaults/.vnc/xstartup", os.path.join(vnc_dir, "xstartup"))
    os.chmod(os.path.join(vnc_dir, "passwd"), 0o600)
    os.chmod(os.path.join(vnc_dir, "xstartup"), 0o755)

    if os.geteuid() == 0:
        code, output = run_cmd(
            f"chown -R {shlex.quote(nb_uid)}:{shlex.quote(nb_gid)} {shlex.quote(home_dir)}"
        )
        assert code == 0, f"Failed to set home ownership for {home_dir}: {output}"

    if os.path.lexists("/etc/guacamole/user-mapping.xml"):
        os.unlink("/etc/guacamole/user-mapping.xml")
    os.symlink("/etc/guacamole/user-mapping-vnc-rdp.xml", "/etc/guacamole/user-mapping.xml")

    return nb_user, home_dir

def test_vnc_binaries_exist():
    """Verify VNC and RDP binaries are installed."""
    expected_cmds = [
        "vncserver",
        "Xvnc",
        "vncpasswd"
    ]
    for cmd in expected_cmds:
        code, _ = run_cmd(f"command -v {cmd}")
        assert code == 0, f"VNC command missing: {cmd}"

def test_rdp_binaries_exist():
    """Verify xrdp related binaries are installed."""
    expected_cmds = [
        "xrdp",
        "xrdp-sesman"
    ]
    for cmd in expected_cmds:
        code, _ = run_cmd(f"command -v {cmd}")
        assert code == 0, f"RDP command missing: {cmd}"

def test_guacamole_config_exists():
    """Verify Guacamole configuration exists."""
    assert os.path.exists("/etc/guacamole/guacd.conf"), "Guacamole guacd.conf missing"
    assert os.path.exists("/etc/guacamole/user-mapping-vnc.xml"), "Guacamole user-mapping missing"


def test_guacamole_protocol_modules_have_runtime_dependencies():
    """Verify Guacamole's RDP and VNC protocol modules can resolve their shared libraries."""
    modules = [
        "/usr/local/lib/libguac-client-rdp.so.0.0.0",
        "/usr/local/lib/libguac-client-vnc.so.0.0.0",
    ]

    for module in modules:
        assert os.path.exists(module), f"Guacamole protocol module missing: {module}"

        code, output = run_cmd(f"ldd {shlex.quote(module)}")
        assert code == 0, f"ldd failed for {module}: {output}"

        missing = [
            line.strip()
            for line in output.splitlines()
            if "=> not found" in line
        ]
        assert not missing, (
            f"Guacamole protocol module has unresolved runtime libraries: {module}\n"
            + "\n".join(missing)
        )


def test_guacamole_api_exposes_rdp_and_vnc_connections():
    """Start Guacamole and verify its API exposes both desktop connections."""
    if os.geteuid() != 0:
        pytest.skip("Guacamole API smoke test requires running as root inside the container")

    code, output = run_cmd("command -v service")
    if code != 0:
        pytest.skip(f"Guacamole API smoke test requires service management support: {output}")

    nb_user, home_dir = _prepare_guacamole_runtime()

    env = os.environ.copy()
    env.setdefault("NB_USER", nb_user)
    env.setdefault("NB_UID", "1000")
    env.setdefault("NB_GID", "100")
    env["HOME"] = home_dir

    process = subprocess.Popen(
        ["/opt/neurodesktop/guacamole.sh"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    try:
        _guacamole_ready()
        _wait_for_tcp_port(4822)
        _wait_for_tcp_port(3389)
        _wait_for_tcp_port(5901)
        auth_response = _guacamole_login(nb_user, "password")

        token = auth_response["authToken"]
        data_source = auth_response["dataSource"]
        request_url = (
            f"http://127.0.0.1:8080/api/session/data/{data_source}/connections"
            f"?token={urllib.parse.quote(token)}"
        )
        with urllib.request.urlopen(request_url, timeout=10) as response:
            connections = json.load(response)

        protocols = sorted(
            connection["protocol"] for connection in connections.values()
        )
        assert protocols == ["rdp", "vnc"], (
            "Guacamole API should expose both desktop connections, "
            f"found: {protocols}"
        )
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=10)

        run_cmd("pkill -f 'guacd -b 127.0.0.1' || true")
        run_cmd("pkill -f 'Xtigervnc' || true")
        run_cmd("vncserver -kill :1 >/dev/null 2>&1 || true")
        run_cmd("service xrdp stop >/dev/null 2>&1 || true")
        run_cmd("pkill -f '/usr/local/tomcat' || true")

        if process.returncode not in (0, -15):
            pytest.fail(
                "Guacamole smoke test process exited unexpectedly.\n"
                f"Output:\n{output}"
            )

def test_vnc_startup(tmp_path):
    """Start up a temporary VNC session to ensure it runs without crashing."""
    pwd_file = tmp_path / "vncpasswd"
    
    # Needs a vnc password
    code, output = run_cmd(f"printf 'password\\npassword\\n\\n' | vncpasswd {pwd_file}")
    assert code == 0, f"Could not generate vncpasswd: {output}"
    
    # Pick a random display port like :99
    # Use the container's default xstartup instead of ~/.vnc/xstartup because restore_home_defaults hasn't run
    code, output = run_cmd(f"USER=jovyan HOME={tmp_path} vncserver -xstartup /opt/jovyan_defaults/.vnc/xstartup -rfbauth {pwd_file} :99")
    assert code == 0, f"VNC server failed to start: {output}"
    
    # Give it a second
    time.sleep(2)
    
    # Check if the process Xtigervnc or vncserver is running for display :99
    code, output = run_cmd("ps auxww | grep -v grep | grep -E 'Xtigervnc.*:99'")
    if code != 0:
        _, log_content = run_cmd(f"cat {tmp_path}/.vnc/*:99.log || true")
        assert False, f"Xtigervnc :99 process is not running. VNC startup crashed.\\nLog:\\n{log_content}\\n\\nPS Output:\\n{output}"
    
    # Clean up
    run_cmd("vncserver -kill :99")
