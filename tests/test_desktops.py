import subprocess
import os
import pwd
import shlex
import json
import socket
import shutil
import time
import urllib.parse
import urllib.request
import pytest
import websocket


def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()


def _can_run_root_cmds():
    if os.geteuid() == 0:
        return True

    code, _ = run_cmd("sudo -n true")
    return code == 0


def _run_root_cmd(cmd):
    if os.geteuid() == 0:
        return run_cmd(cmd)

    if not _can_run_root_cmds():
        return 1, "Passwordless sudo is unavailable"

    return run_cmd(f"sudo -n bash -lc {shlex.quote(cmd)}")


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
    root_cmds_available = _can_run_root_cmds()
    home_dir = os.path.expanduser(f"~{nb_user}")
    vnc_dir = os.path.join(home_dir, ".vnc")
    ssh_dir = os.path.join(home_dir, ".ssh")
    guacamole_mapping = "/etc/guacamole/user-mapping-vnc.xml"

    os.makedirs(vnc_dir, exist_ok=True)
    os.makedirs(ssh_dir, exist_ok=True)
    if root_cmds_available:
        code, output = _run_root_cmd("mkdir -p /var/run/sshd")
        assert code == 0, f"Failed to create /var/run/sshd: {output}"
        guacamole_mapping = "/etc/guacamole/user-mapping-vnc-rdp.xml"

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
    os.symlink(guacamole_mapping, "/etc/guacamole/user-mapping.xml")

    return nb_user, home_dir, root_cmds_available


def _start_guacamole_as_user(nb_user, home_dir):
    command = (
        f"export HOME={shlex.quote(home_dir)} "
        f"NB_USER={shlex.quote(nb_user)} "
        f"NB_UID={shlex.quote(os.environ.get('NB_UID', '1000'))} "
        f"NB_GID={shlex.quote(os.environ.get('NB_GID', '100'))}; "
        "exec /opt/neurodesktop/guacamole.sh"
    )
    current_user = pwd.getpwuid(os.geteuid()).pw_name

    if current_user == nb_user:
        return subprocess.Popen(
            ["/bin/bash", "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    if os.geteuid() == 0:
        return subprocess.Popen(
            ["su", "-s", "/bin/bash", "-c", command, nb_user],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    raise RuntimeError(
        f"Cannot switch from {current_user} to {nb_user} without root privileges."
    )


def _ensure_rdp_backend_ready():
    try:
        _wait_for_tcp_port(3389, timeout_seconds=2)
        return
    except AssertionError:
        pass

    code, output = _run_root_cmd("/opt/neurodesktop/ensure_rdp_backend.sh")
    assert code == 0, f"Failed to initialize XRDP backend before Guacamole launch: {output}"
    _wait_for_tcp_port(3389)


def _guacamole_connections(data_source, token):
    request_url = (
        f"http://127.0.0.1:8080/api/session/data/{data_source}/connections"
        f"?token={urllib.parse.quote(token)}"
    )
    with urllib.request.urlopen(request_url, timeout=10) as response:
        return json.load(response)


def _connection_id_by_protocol(connections, protocol):
    for connection_id, connection in connections.items():
        if connection.get("protocol") == protocol:
            return connection_id
    raise AssertionError(f"Guacamole connection with protocol {protocol!r} not found: {connections}")


def _open_guacamole_tunnel(token, data_source, connection_id, width=1280, height=1024):
    params = [
        ("token", token),
        ("GUAC_DATA_SOURCE", data_source),
        ("GUAC_ID", connection_id),
        ("GUAC_TYPE", "c"),
        ("GUAC_WIDTH", str(width)),
        ("GUAC_HEIGHT", str(height)),
        ("GUAC_DPI", "96"),
        ("GUAC_TIMEZONE", os.environ.get("TZ", "UTC")),
    ]
    query_string = urllib.parse.urlencode(params, doseq=True)

    tunnel = websocket.create_connection(
        f"ws://127.0.0.1:8080/websocket-tunnel?{query_string}",
        subprotocols=["guacamole"],
        timeout=10,
    )
    tunnel.settimeout(2)
    return tunnel


def _collect_guacamole_frames(tunnel, timeout_seconds=8):
    deadline = time.time() + timeout_seconds
    frames = []

    while time.time() < deadline:
        try:
            frame = tunnel.recv()
        except websocket.WebSocketTimeoutException:
            continue

        frames.append(frame)
        if isinstance(frame, str) and any(
            opcode in frame for opcode in (".sync,", ".img,", ".size,", ".mouse,")
        ):
            return frames

    raise AssertionError(
        "Guacamole RDP tunnel did not deliver desktop instructions. "
        f"Frames received: {frames}"
    )


def _read_xrdp_log():
    code, output = _run_root_cmd("tail -n 200 /var/log/xrdp.log")
    assert code == 0, f"Failed to read /var/log/xrdp.log: {output}"
    return output


def _cleanup_guacamole_process(process):
    output = ""

    if process.poll() is None:
        process.terminate()
    try:
        output, _ = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=10)

    run_cmd("pkill -f 'guacd -b 127.0.0.1' || true")
    run_cmd("pkill -f 'Xtigervnc' || true")
    run_cmd("vncserver -kill :1 >/dev/null 2>&1 || true")
    _run_root_cmd("service xrdp stop >/dev/null 2>&1 || true")
    run_cmd("pkill -f '/usr/local/tomcat' || true")

    if process.returncode not in (0, -15):
        pytest.fail(
            "Guacamole smoke test process exited unexpectedly.\n"
            f"Output:\n{output}"
        )

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


def test_guacamole_api_exposes_available_desktop_connections():
    """Start Guacamole and verify its API exposes the desktop protocols this runtime can support."""
    nb_user, home_dir, root_cmds_available = _prepare_guacamole_runtime()
    expected_protocols = ["vnc"]

    if root_cmds_available:
        _ensure_rdp_backend_ready()
        expected_protocols.insert(0, "rdp")

    process = _start_guacamole_as_user(nb_user, home_dir)
    try:
        _guacamole_ready()
        _wait_for_tcp_port(4822)
        _wait_for_tcp_port(5901)
        auth_response = _guacamole_login(nb_user, "password")
        connections = _guacamole_connections(
            auth_response["dataSource"], auth_response["authToken"]
        )

        protocols = sorted(
            connection["protocol"] for connection in connections.values()
        )
        assert protocols == expected_protocols, (
            "Guacamole API exposed unexpected desktop connections, "
            f"found: {protocols}"
        )
    finally:
        _cleanup_guacamole_process(process)


def test_xrdp_tls_key_access_is_configured():
    """Verify xrdp can read its TLS private key via ssl-cert group membership."""
    code, output = run_cmd("id -nG xrdp")
    assert code == 0, f"Failed to inspect xrdp group membership: {output}"
    assert "ssl-cert" in output.split(), (
        "xrdp must belong to the ssl-cert group so it can read /etc/xrdp/key.pem. "
        f"Groups: {output}"
    )


def test_guacamole_rdp_tunnel_establishes_desktop_session():
    """Verify the Guacamole RDP tunnel renders a desktop without TLS key permission errors."""
    nb_user, home_dir, root_cmds_available = _prepare_guacamole_runtime()

    if not root_cmds_available:
        pytest.skip("RDP smoke test requires root or passwordless sudo to start xrdp")

    _ensure_rdp_backend_ready()

    process = _start_guacamole_as_user(nb_user, home_dir)
    tunnel = None
    try:
        _guacamole_ready()
        _wait_for_tcp_port(4822)

        auth_response = _guacamole_login(nb_user, "password")
        connections = _guacamole_connections(
            auth_response["dataSource"], auth_response["authToken"]
        )
        rdp_connection_id = _connection_id_by_protocol(connections, "rdp")

        tunnel = _open_guacamole_tunnel(
            auth_response["authToken"],
            auth_response["dataSource"],
            rdp_connection_id,
        )
        frames = _collect_guacamole_frames(tunnel)
        assert any(
            isinstance(frame, str)
            and any(opcode in frame for opcode in (".sync,", ".img,", ".size,", ".mouse,"))
            for frame in frames
        ), f"Guacamole RDP tunnel did not render desktop output: {frames}"

        xrdp_log = _read_xrdp_log()
        assert "Cannot read private key file /etc/xrdp/key.pem: Permission denied" not in xrdp_log, xrdp_log
        assert (
            "Cannot accept TLS connections because certificate or private key file is not readable"
            not in xrdp_log
        ), xrdp_log
    finally:
        if tunnel is not None:
            try:
                tunnel.close()
            except Exception:
                pass
        _cleanup_guacamole_process(process)

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
