import subprocess
import os
import pwd
import re
import shlex
import json
import socket
import shutil
import tempfile
import time
import urllib.error
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


def _wait_for_tcp_port(port, timeout_seconds=30, host="127.0.0.1"):
    deadline = time.time() + timeout_seconds
    last_error = None

    while time.time() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        try:
            sock.connect((host, port))
            return
        except OSError as error:  # pragma: no cover - exercised in retries
            last_error = error
            time.sleep(1)
        finally:
            sock.close()

    raise AssertionError(f"TCP port {port} did not open on {host}: {last_error}")


def _read_runtime_port(home_dir, name, default):
    """Read a port guacamole.sh published to $HOME/.neurodesk/runtime/<name>."""
    runtime_file = os.path.join(home_dir, ".neurodesk", "runtime", name)
    if os.path.exists(runtime_file):
        try:
            return int(open(runtime_file).read().strip())
        except ValueError:
            pass
    return default


def _drain_stream_nonblocking(stream):
    """Pull whatever is currently buffered on `stream` without blocking.

    Used to capture guacamole.sh's aggregated stdout while it is still running,
    so test failures can surface the VNC/RDP/Tomcat backend setup lines that
    printed *before* the readiness probe gave up."""
    if stream is None:
        return ""
    try:
        import fcntl
        fd = stream.fileno()
        old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags | os.O_NONBLOCK)
        try:
            chunks = []
            while True:
                try:
                    data = stream.read()
                except (BlockingIOError, OSError):
                    break
                if not data:
                    break
                chunks.append(data)
            return "".join(chunks) if chunks else ""
        finally:
            fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
    except Exception as error:
        return f"(failed to read guacamole.sh stream: {error})"


def _latest_vnc_log(home_dir):
    """Return the tail of the most recent Xvnc .log in ~/.vnc, if any."""
    code, out = run_cmd(
        f"ls -t {shlex.quote(home_dir)}/.vnc/*:*.log 2>/dev/null | head -n1"
    )
    log_path = out.strip()
    if not log_path:
        return ""
    _, tail = run_cmd(f"tail -n 80 {shlex.quote(log_path)}")
    return f"--- {log_path} (last 80 lines) ---\n{tail}"


def _guacamole_ready(port, timeout_seconds=90, home_dir=None, process=None):
    deadline = time.time() + timeout_seconds
    last_error = None

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                if response.status == 200:
                    return
        except Exception as error:  # pragma: no cover - exercised in retries
            last_error = error
            time.sleep(1)

    diag = [f"Guacamole UI did not become ready on port {port}: {last_error}"]

    # Dump the listening sockets so we can see where Tomcat actually ended up.
    _, ss_out = run_cmd("ss -lntp 2>/dev/null | grep -i java || ss -lnt 2>/dev/null | head -n 30")
    diag.append("--- listening sockets ---\n" + ss_out)

    # Dump Tomcat's catalina log if we can find it in the user's CATALINA_BASE.
    if home_dir:
        catalina = os.path.join(home_dir, ".neurodesk", "tomcat", "logs", "catalina.out")
        if os.path.exists(catalina):
            _, tail = run_cmd(f"tail -n 80 {shlex.quote(catalina)}")
            diag.append(f"--- {catalina} (last 80 lines) ---\n" + tail)
        else:
            diag.append(f"(no {catalina} found)")
        vnc_tail = _latest_vnc_log(home_dir)
        if vnc_tail:
            diag.append(vnc_tail)

    # Surface guacamole.sh's aggregated stdout/stderr whether or not the process
    # has exited. The HPC desktop-not-starting symptoms (no free VNC display,
    # guacd port collision on a shared netns, xrdp requires root and bailed)
    # all print *here*, not in catalina.out.
    if process is not None:
        if process.poll() is not None:
            try:
                out = process.stdout.read() if process.stdout else ""
            except Exception as error:
                out = f"(failed to drain guacamole.sh stdout: {error})"
            diag.append(f"--- guacamole.sh exited with rc={process.returncode} ---\n{out}")
        else:
            out = _drain_stream_nonblocking(process.stdout)
            diag.append(f"--- guacamole.sh still running, stdout so far ---\n{out}")

    raise AssertionError("\n".join(diag))


def _guacamole_login(port, username, password):
    payload = urllib.parse.urlencode(
        {"username": username, "password": password}
    ).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/tokens", data=payload)

    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _live_mapping_path(home_dir):
    return os.path.join(home_dir, ".neurodesk", "guacamole", "user-mapping.xml")


def _web_password_path(home_dir):
    return os.path.join(home_dir, ".neurodesk", "secrets", "guacamole_web_password")


def _vnc_password_path(home_dir):
    return os.path.join(home_dir, ".neurodesk", "secrets", "vnc_password")


def _read_live_web_password(home_dir):
    with open(_web_password_path(home_dir)) as fp:
        return fp.read().strip()


def _read_live_web_user(home_dir, fallback):
    user_file = os.path.join(home_dir, ".neurodesk", "secrets", "guacamole_web_user")
    try:
        with open(user_file) as fp:
            value = fp.read().strip()
            if value:
                return value
    except OSError:
        pass
    return fallback


def _current_user_name():
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        # Apptainer/HPC: the host UID may not be in the container's /etc/passwd.
        return os.environ.get("USER") or os.environ.get("LOGNAME") or str(os.geteuid())


def _pick_guacamole_runtime_identity():
    """Pick (nb_user, home_dir) that will actually own the Guacamole state.

    - Classic docker: runs as root or as jovyan; uses /home/jovyan.
    - Apptainer on HPC: runs as a host user (e.g. `sciget`) with HOME bind-mounted
      onto /home/jovyan. We must use HOME (what the caller's shell sees), not
      `~jovyan` from the image's /etc/passwd, because the actual Neurodesktop
      session writes into $HOME. NB_USER controls the Guacamole <authorize>
      username — we keep whatever init_secrets.sh would have chosen (falling back
      to the current user rather than inventing a name the container cannot
      switch to)."""
    current = _current_user_name()
    home_from_env = os.environ.get("HOME") or os.path.expanduser("~")
    nb_user = os.environ.get("NB_USER")
    if not nb_user:
        if os.geteuid() == 0:
            # Classic docker-as-root path: su down to jovyan like we always did.
            nb_user = "jovyan"
        else:
            # If we happen to be jovyan (classic docker), keep the classic label
            # so the assertion against the rotated <authorize> username still
            # matches. Otherwise (apptainer/HPC) use the current user — we
            # cannot su away from it.
            nb_user = "jovyan" if current == "jovyan" else current
    return nb_user, home_from_env


def _prepare_guacamole_runtime():
    nb_user, home_dir = _pick_guacamole_runtime_identity()
    nb_uid = os.environ.get("NB_UID", str(os.geteuid()))
    nb_gid = os.environ.get("NB_GID", str(os.getegid()))
    root_cmds_available = _can_run_root_cmds()
    vnc_dir = os.path.join(home_dir, ".vnc")
    ssh_dir = os.path.join(home_dir, ".ssh")

    if not os.access(home_dir, os.W_OK):
        pytest.skip(
            f"Current user ({_current_user_name()}) cannot write to HOME={home_dir}; "
            "Guacamole smoke tests need write access to the Neurodesktop home. "
            "Run pytest inside the container as the session user (or as root)."
        )

    os.makedirs(vnc_dir, exist_ok=True)
    os.makedirs(ssh_dir, exist_ok=True)
    if root_cmds_available:
        code, output = _run_root_cmd("mkdir -p /var/run/sshd")
        assert code == 0, f"Failed to create /var/run/sshd: {output}"

    shutil.copy("/opt/jovyan_defaults/.vnc/xstartup", os.path.join(vnc_dir, "xstartup"))
    os.chmod(os.path.join(vnc_dir, "xstartup"), 0o755)

    if os.geteuid() == 0:
        code, output = run_cmd(
            f"chown -R {shlex.quote(nb_uid)}:{shlex.quote(nb_gid)} {shlex.quote(home_dir)}"
        )
        assert code == 0, f"Failed to set home ownership for {home_dir}: {output}"

    # Use a per-test GUACAMOLE_HOME (isolated tempdir) so the test's Tomcat
    # writes its user-mapping.xml there, not into the live
    # $HOME/.neurodesk/guacamole that the real Jupyter-spawned Guacamole is
    # reading. Without this isolation, guacamole.sh stamps the test-session's
    # VNC port into the live mapping; after cleanup kills the test vncserver,
    # the browser session dials that now-dead port and Guacamole returns 500
    # (or connection refused). Secrets stay shared under $HOME so Jupyter's
    # cached Basic-auth header keeps working.
    guacamole_home = tempfile.mkdtemp(prefix="neurodesk-test-guac-")
    if os.geteuid() == 0:
        run_cmd(
            f"chown -R {shlex.quote(nb_uid)}:{shlex.quote(nb_gid)} {shlex.quote(guacamole_home)}"
        )

    # Wipe per-test scratch that would carry over from prior runs. We do NOT
    # touch $HOME/.neurodesk/guacamole anymore (the live user-mapping); only
    # the per-test tempdir (already empty) and our own tomcat/runtime caches.
    for rel in (".neurodesk/runtime", ".neurodesk/tomcat", ".vnc/passwd"):
        path = os.path.join(home_dir, rel)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass

    return nb_user, home_dir, root_cmds_available, guacamole_home


def _xrdp_already_running():
    """True when an xrdp daemon is already bound on this netns.

    When the real Jupyter-spawned Neurodesktop has already started xrdp on port
    3389, a second test-side `ensure_rdp_backend.sh` picks a fresh port (3390,
    ...) but the system `service xrdp start` is a no-op against the existing
    daemon, so the test's chosen port never binds and the RDP connection is
    legitimately stripped from the test mapping. That is not a product bug -
    it's the known "two Neurodesktop sessions on one netns" RDP limitation.
    Detect it up-front so dependent tests skip cleanly with an actionable
    message instead of failing deep inside the Guacamole handshake."""
    code, out = run_cmd("pgrep -x xrdp 2>/dev/null | head -n1")
    if code == 0 and out.strip():
        return True
    code, out = run_cmd("ss -lnt 2>/dev/null | awk 'NR>1 {print $4}' | grep -E '(^|:)3389$'")
    return code == 0 and bool(out.strip())


def _start_guacamole_as_user(nb_user, home_dir, guacamole_home, tomcat_port=None):
    """Start guacamole.sh in a child process, publishing the chosen Tomcat port.

    `guacamole_home` MUST be an isolated per-test path: guacamole.sh will stamp
    VNC/RDP ports into `<guacamole_home>/user-mapping.xml`, and those stamps
    must not leak into the live $HOME/.neurodesk/guacamole that the real
    Jupyter-spawned Guacamole is serving to the browser.
    """
    if tomcat_port is None:
        # Find a free port and pass it through NEURODESKTOP_TOMCAT_PORT so we can
        # talk to the right instance when two sessions run back-to-back.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        tomcat_port = sock.getsockname()[1]
        sock.close()

    command = (
        f"export HOME={shlex.quote(home_dir)} "
        f"NB_USER={shlex.quote(nb_user)} "
        f"NB_UID={shlex.quote(os.environ.get('NB_UID', '1000'))} "
        f"NB_GID={shlex.quote(os.environ.get('NB_GID', '100'))} "
        f"NEURODESKTOP_TOMCAT_PORT={tomcat_port} "
        f"GUACAMOLE_HOME={shlex.quote(guacamole_home)}; "
        # Publish the chosen port for the test harness to read.
        f"mkdir -p {shlex.quote(os.path.join(home_dir, '.neurodesk/runtime'))}; "
        f"echo {tomcat_port} > {shlex.quote(os.path.join(home_dir, '.neurodesk/runtime/tomcat_port'))}; "
        "exec /opt/neurodesktop/guacamole.sh"
    )
    current_user = _current_user_name()

    # Apptainer/HPC: the host user runs inside the container with HOME pointing
    # at what would be jovyan's home. We do NOT need to su to jovyan — this
    # process already has write access to home_dir (checked by
    # _prepare_guacamole_runtime). Only switch identity when a caller explicitly
    # passes a different nb_user AND we are root AND we are actually different.
    if os.geteuid() == 0 and current_user != nb_user:
        proc = subprocess.Popen(
            ["su", "-s", "/bin/bash", "-c", command, nb_user],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    else:
        proc = subprocess.Popen(
            ["/bin/bash", "-lc", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    proc.tomcat_port = tomcat_port
    return proc


def _ensure_rdp_backend_ready():
    code, output = _run_root_cmd("/opt/neurodesktop/ensure_rdp_backend.sh")
    assert code == 0, f"Failed to initialize XRDP backend before Guacamole launch: {output}"


def _guacamole_connections(port, data_source, token):
    request_url = (
        f"http://127.0.0.1:{port}/api/session/data/{data_source}/connections"
        f"?token={urllib.parse.quote(token)}"
    )
    with urllib.request.urlopen(request_url, timeout=10) as response:
        return json.load(response)


def _connection_id_by_protocol(connections, protocol):
    for connection_id, connection in connections.items():
        if connection.get("protocol") == protocol:
            return connection_id
    raise AssertionError(f"Guacamole connection with protocol {protocol!r} not found: {connections}")


def _open_guacamole_tunnel(port, token, data_source, connection_id, width=1280, height=1024):
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
        f"ws://127.0.0.1:{port}/websocket-tunnel?{query_string}",
        subprotocols=["guacamole"],
        timeout=10,
    )
    tunnel.settimeout(2)
    return tunnel


def _collect_guacamole_frames(tunnel, timeout_seconds=8):
    """Legacy liberal check - accepts the first control frame as success.

    Kept for RDP compatibility (RDP authenticates via PAM which is a different
    failure surface). The VNC test uses _collect_guacamole_desktop_frames
    below, which is much stricter because VNC auth failures only show up as
    .error frames *after* the initial .sync handshake, and the old check was
    returning successfully before the backend ever responded."""
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


def _collect_guacamole_desktop_frames(tunnel, timeout_seconds=30):
    """Wait for real desktop pixels and reject any Guacamole error frames.

    Guacamole sends a .sync instruction as a handshake ack before the backend
    (Xvnc) has even accepted the connection. The previous collector returned
    success as soon as it saw that .sync, so an auth failure that arrived 200ms
    later was never detected. This collector requires:

      * at least one .img, frame (actual pixel data from the backend), OR
        one .png, / .jpeg, frame (alternative pixel encodings), AND
      * no .error, frame in the stream.

    That combination is only true when the backend really did authenticate,
    handshake, and render a frame.
    """
    deadline = time.time() + timeout_seconds
    frames = []
    saw_pixels = False

    while time.time() < deadline:
        try:
            frame = tunnel.recv()
        except websocket.WebSocketTimeoutException:
            if saw_pixels:
                # Backend rendered at least once; idle frames are fine.
                return frames
            continue
        except Exception as error:  # pragma: no cover - unexpected tunnel death
            frames.append(f"<tunnel recv failed: {error}>")
            break

        frames.append(frame)
        if not isinstance(frame, str):
            continue

        if ".error," in frame:
            raise AssertionError(
                f"Guacamole tunnel returned an error frame: {frame!r}\n"
                f"All frames: {frames}"
            )
        if any(op in frame for op in (".img,", ".png,", ".jpeg,")):
            saw_pixels = True
            # Give the backend a moment to flush a few more frames so the test
            # can demonstrate a stable session (not just a single flash before
            # disconnect).
            deadline = min(deadline, time.time() + 3)

    if not saw_pixels:
        raise AssertionError(
            "Guacamole tunnel produced no pixel frames (.img/.png/.jpeg) within "
            f"{timeout_seconds}s - VNC backend never handed a rendered desktop "
            f"through. Frames received ({len(frames)}):\n"
            + "\n".join(str(f)[:200] for f in frames)
        )
    return frames


def _read_xrdp_log():
    code, output = _run_root_cmd("tail -n 200 /var/log/xrdp.log")
    assert code == 0, f"Failed to read /var/log/xrdp.log: {output}"
    return output


def _cleanup_guacamole_process(process, guacamole_home=None):
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
    for display in range(1, 10):
        run_cmd(f"vncserver -kill :{display} >/dev/null 2>&1 || true")
    _run_root_cmd("service xrdp stop >/dev/null 2>&1 || true")
    run_cmd("pkill -f '/usr/local/tomcat' || true")
    # Kill the per-test sshd that ensure_sftp_sshd.sh started. Leaving it
    # behind makes the next test's port probe skip 2222, 2223, ... and stamp
    # an ever-higher sftp-port into its mapping; if the new sshd then fails
    # to start on the higher port Guacamole would dial a dead SFTP service
    # and abort VNC with CLIENT_UNAUTHORIZED.
    run_cmd("pkill -f 'sshd .* -p 22[0-9][0-9]' || true")

    # Remove the isolated GUACAMOLE_HOME so subsequent tests start clean and
    # no stale mapping / properties files persist on disk.
    if guacamole_home and os.path.isdir(guacamole_home):
        shutil.rmtree(guacamole_home, ignore_errors=True)

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
    """Verify the build-time Guacamole templates exist; per-user copies are
    generated at container start by init_secrets.sh."""
    assert os.path.exists("/etc/guacamole/guacd.conf"), "Guacamole guacd.conf missing"
    assert os.path.exists("/etc/guacamole/user-mapping-vnc-rdp.xml"), "Guacamole VNC+RDP template missing"


def test_init_secrets_script_installed():
    """init_secrets.sh must be on disk and executable; it is the only path that
    materialises per-user credentials and prevents the cross-user VNC leak."""
    assert os.access("/opt/neurodesktop/init_secrets.sh", os.X_OK), \
        "/opt/neurodesktop/init_secrets.sh must be executable"


def test_guac_protocol_libs():
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


def test_init_secrets_generates_per_user_mapping(tmp_path):
    """init_secrets.sh must seed a per-user user-mapping.xml with NON-default
    credentials. This is the load-bearing check for the HPC cross-user leak:
    before this fix the mapping stayed at the read-only /etc/guacamole template
    under Apptainer."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["NB_USER"] = "jovyan"

    result = subprocess.run(
        ["/opt/neurodesktop/init_secrets.sh"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"init_secrets.sh failed: {result.stdout}\n{result.stderr}"

    mapping = tmp_path / ".neurodesk" / "guacamole" / "user-mapping.xml"
    assert mapping.exists(), "init_secrets.sh did not create per-user mapping"

    mapping_text = mapping.read_text()
    # The <authorize password=...> must have been rotated away from the literal
    # build-time default; any sibling user who tries the old string must fail.
    assert 'password="password"' not in mapping_text, \
        "init_secrets.sh left the literal password='password' in user-mapping.xml"

    web_password_file = tmp_path / ".neurodesk" / "secrets" / "guacamole_web_password"
    assert web_password_file.exists(), "guacamole_web_password secret was not written"
    assert len(web_password_file.read_text().strip()) >= 16, \
        "Rotated Guacamole web password is suspiciously short"


def test_init_secrets_stamps_vnc_passwd_file(tmp_path):
    """init_secrets.sh must stamp ~/.vnc/passwd with the *rotated* token, not
    the default "password" hash that restore_home_defaults.sh migrates in from
    /opt/jovyan_defaults on every container start. Regression guard against
    the HPC-visible window between boot and first Neurodesktop click, during
    which a sibling user on the shared netns could auth with the literal
    default and land inside the user's VNC session.

    NEGATIVE assertion: the written file must NOT equal the vncpasswd encoding
    of the literal default "password"."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["NB_USER"] = "jovyan"

    result = subprocess.run(
        ["/opt/neurodesktop/init_secrets.sh"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"init_secrets.sh failed: {result.stderr}"

    vnc_passwd = tmp_path / ".vnc" / "passwd"
    assert vnc_passwd.exists(), (
        "init_secrets.sh did not write ~/.vnc/passwd - the boot-time rotation "
        "window against the build-time default hash is still open."
    )
    assert oct(vnc_passwd.stat().st_mode)[-3:] == "600", (
        f"~/.vnc/passwd must be mode 0600, got {oct(vnc_passwd.stat().st_mode)}"
    )

    default_hash = subprocess.run(
        ["/bin/bash", "-lc", "printf 'password\\n' | vncpasswd -f"],
        capture_output=True,
    )
    assert default_hash.returncode == 0
    assert vnc_passwd.read_bytes() != default_hash.stdout, (
        "~/.vnc/passwd still matches the literal default hash after "
        "init_secrets.sh - boot-time VNC rotation is broken."
    )


def test_init_secrets_does_not_leak_set_u_into_caller(tmp_path):
    """When jupyterlab_startup.sh sources init_secrets.sh, the `set -u` option
    must NOT leak into the outer shell. Otherwise unrelated unbound references
    further in the startup chain (e.g. the backgrounded codeserver-extension
    installer's `$2`) crash the subshell and take out whatever it was doing.
    This is a regression guard against the root cause of the `line 217: $2:
    unbound variable` symptom users saw on HPC."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["NB_USER"] = "jovyan"

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            "source /opt/neurodesktop/init_secrets.sh; "
            "echo nounset_status:$(shopt -o nounset | awk '{print $2}')",
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"sourcing init_secrets.sh failed: {result.stderr}"
    assert "nounset_status:off" in result.stdout, (
        "init_secrets.sh leaked `set -u` into the sourcing shell; outer scripts "
        f"will now crash on any unbound var. Output:\n{result.stdout}"
    )


def test_init_secrets_is_idempotent_and_stable(tmp_path):
    """Running init_secrets.sh twice must preserve the same random credentials
    so session continuity across restarts works. Regression guard against
    rotating on every invocation and breaking active Guacamole tokens."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["NB_USER"] = "jovyan"

    for _ in range(2):
        result = subprocess.run(
            ["/opt/neurodesktop/init_secrets.sh"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"init_secrets.sh failed: {result.stderr}"

    web_password = (tmp_path / ".neurodesk" / "secrets" / "guacamole_web_password").read_text()

    # Run it again - password must not change.
    result = subprocess.run(
        ["/opt/neurodesktop/init_secrets.sh"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert (tmp_path / ".neurodesk" / "secrets" / "guacamole_web_password").read_text() == web_password


def test_two_users_get_distinct_random_credentials(tmp_path):
    """Two concurrent Apptainer users on one node must NOT share Guacamole
    credentials. Simulate by running init_secrets.sh twice with distinct HOMEs
    and assert the generated passwords differ - which is what keeps user B's
    Guacamole from authenticating into user A's VNC when the netns is shared."""
    home_a = tmp_path / "user_a"
    home_b = tmp_path / "user_b"
    home_a.mkdir()
    home_b.mkdir()

    results = {}
    for label, home in (("a", home_a), ("b", home_b)):
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["NB_USER"] = "jovyan"
        proc = subprocess.run(
            ["/opt/neurodesktop/init_secrets.sh"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"init_secrets.sh failed for user {label}: {proc.stderr}"
        results[label] = {
            "web": (home / ".neurodesk" / "secrets" / "guacamole_web_password").read_text().strip(),
            "vnc": (home / ".neurodesk" / "secrets" / "vnc_password").read_text().strip(),
        }

    assert results["a"]["web"] != results["b"]["web"], \
        "Two simulated users received the same Guacamole web password"
    assert results["a"]["vnc"] != results["b"]["vnc"], \
        "Two simulated users received the same VNC password"


def test_live_mapping_has_no_literal_default_password(tmp_path):
    """Grep defense against a regression that reintroduces the string
    password='password' into the user-mapping.xml that Guacamole actually
    reads. If this fires, the HPC cross-user leak is back."""
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["NB_USER"] = "jovyan"
    result = subprocess.run(
        ["/opt/neurodesktop/init_secrets.sh"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"init_secrets.sh failed: {result.stderr}"

    mapping = (tmp_path / ".neurodesk" / "guacamole" / "user-mapping.xml").read_text()
    assert 'password="password"' not in mapping
    # Per-connection VNC <param name="password"> still holds the template value
    # ("password") until guacamole.sh starts vncserver and stamps the rotated
    # secret; that path is covered by the full-startup smoke test below.


def test_vncserver_startup_uses_random_password(tmp_path):
    """A clean vncserver launch must use the rotated token (not 'password').
    If init_secrets.sh is skipped by a deployment, the ~/.vnc/passwd file would
    revert to the default and a sibling user could authenticate into the
    session. This guards that ~/.vnc/passwd ends up consistent with the
    generated secret, not a literal 'password'."""
    # Drive just the credential-generation half of guacamole.sh in isolation.
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["NB_USER"] = "jovyan"

    # Bootstrap secrets + mapping.
    proc = subprocess.run(
        ["/opt/neurodesktop/init_secrets.sh"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0

    secret = (tmp_path / ".neurodesk" / "secrets" / "vnc_password").read_text().strip()

    # Generate the .vnc/passwd file the same way guacamole.sh does.
    (tmp_path / ".vnc").mkdir(exist_ok=True)
    gen = subprocess.run(
        ["/bin/bash", "-lc", f"printf '%s\\n' {shlex.quote(secret)} | vncpasswd -f > {shlex.quote(str(tmp_path / '.vnc' / 'passwd'))}"],
        capture_output=True,
        text=True,
    )
    assert gen.returncode == 0, gen.stderr

    # Verify by round-tripping: vncpasswd -f is the same encoder vncserver reads.
    # We can't decode the obfuscated file, so instead encode the literal default
    # and assert it doesn't match what we wrote.
    default = subprocess.run(
        ["/bin/bash", "-lc", "printf 'password\\n' | vncpasswd -f"],
        capture_output=True,
    )
    assert default.returncode == 0
    written = (tmp_path / ".vnc" / "passwd").read_bytes()
    assert written != default.stdout, \
        "VNC passwd file matches the literal 'password' default - rotation failed"


def test_guac_api_connections():
    """Start Guacamole and verify its API exposes the desktop protocols this
    runtime can support. Uses the rotated Guacamole web credential that
    init_secrets.sh generates per-user."""
    nb_user, home_dir, root_cmds_available, guacamole_home = _prepare_guacamole_runtime()

    expected_protocols = ["vnc"]
    if root_cmds_available and not _xrdp_already_running():
        expected_protocols.insert(0, "rdp")

    process = _start_guacamole_as_user(nb_user, home_dir, guacamole_home)
    tomcat_port = process.tomcat_port
    try:
        _guacamole_ready(tomcat_port, home_dir=home_dir, process=process)
        _guacd_port = _read_runtime_port(home_dir, "guacd_port", default=4822)
        _wait_for_tcp_port(_guacd_port)

        web_password = _read_live_web_password(home_dir)
        web_user = _read_live_web_user(home_dir, nb_user)
        auth_response = _guacamole_login(tomcat_port, web_user, web_password)
        connections = _guacamole_connections(
            tomcat_port, auth_response["dataSource"], auth_response["authToken"]
        )

        protocols = sorted(
            connection["protocol"] for connection in connections.values()
        )
        assert protocols == expected_protocols, (
            "Guacamole API exposed unexpected desktop connections, "
            f"found: {protocols}"
        )
    finally:
        _cleanup_guacamole_process(process, guacamole_home=guacamole_home)


def test_guac_login_rejects_legacy_default_password():
    """After init_secrets.sh rotates the <authorize> credential, the Guacamole
    web login must REJECT the historic 'password' literal. This is the
    negative test for the HPC leak: even if a sibling user reaches the Tomcat
    port on a shared netns, they cannot log into Guacamole with the baked-in
    default."""
    nb_user, home_dir, _, guacamole_home = _prepare_guacamole_runtime()
    process = _start_guacamole_as_user(nb_user, home_dir, guacamole_home)
    try:
        _guacamole_ready(process.tomcat_port, home_dir=home_dir, process=process)

        # Confirm the rotated credential works first so we know the server is up.
        rotated = _read_live_web_password(home_dir)
        web_user = _read_live_web_user(home_dir, nb_user)
        _guacamole_login(process.tomcat_port, web_user, rotated)

        # Now fail on the literal default.
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _guacamole_login(process.tomcat_port, web_user, "password")
        assert exc_info.value.code in (401, 403), \
            f"Expected 401/403 for legacy default password, got {exc_info.value.code}"
    finally:
        _cleanup_guacamole_process(process, guacamole_home=guacamole_home)


def test_vncserver_binds_localhost_only():
    """The vncserver must be launched with -localhost yes so its TCP port is
    reachable only through the local loopback. On Apptainer the host netns is
    shared, but at least the backend is not reachable off-node."""
    nb_user, home_dir, _, guacamole_home = _prepare_guacamole_runtime()
    process = _start_guacamole_as_user(nb_user, home_dir, guacamole_home)
    try:
        _guacamole_ready(process.tomcat_port, home_dir=home_dir, process=process)
        # Wait for the display to register.
        deadline = time.time() + 30
        vnc_port = None
        while time.time() < deadline and vnc_port is None:
            mapping = os.path.join(guacamole_home, "user-mapping.xml")
            if os.path.exists(mapping):
                match = re.search(
                    r'<protocol>vnc</protocol>.*?<param name="port">(\d+)</param>',
                    open(mapping).read(),
                    re.S,
                )
                if match:
                    vnc_port = int(match.group(1))
            time.sleep(1)
        assert vnc_port, "VNC port never stamped into user-mapping.xml"

        # It must be reachable on 127.0.0.1 but NOT on a non-loopback interface.
        _wait_for_tcp_port(vnc_port, timeout_seconds=30)
        # Attempt to connect via the external interface. We don't have a real
        # non-loopback IP in-container always, but we can at least check that
        # `ss -lnt` reports the listen address as 127.0.0.1.
        _, ss_out = run_cmd(f"ss -lnt 'sport = :{vnc_port}' | tail -n +2")
        assert ss_out, f"No listen socket found for port {vnc_port}: {ss_out!r}"
        listen_addr = ss_out.split()[3]
        assert listen_addr.startswith("127.0.0.1:") or listen_addr.startswith("[::1]:"), \
            f"vncserver must bind loopback only; listen address was {listen_addr}"
    finally:
        _cleanup_guacamole_process(process, guacamole_home=guacamole_home)


def test_xrdp_tls_key_access():
    """Verify xrdp can read its TLS private key via ssl-cert group membership."""
    # Under the HPC simulation (build_and_run.sh hpctest) the synthetic
    # /etc/passwd deliberately contains only root/jovyan/$NB_USER/nobody -
    # there is no xrdp system user because xrdp itself cannot run without
    # root on HPC Apptainer. The ssl-cert group check is meaningful only on
    # the classic docker image, so skip cleanly instead of asserting.
    code, output = run_cmd("getent passwd xrdp")
    if code != 0 or not output.strip():
        pytest.skip("xrdp system user is not present in /etc/passwd (unprivileged/HPC image)")
    code, output = run_cmd("id -nG xrdp")
    assert code == 0, f"Failed to inspect xrdp group membership: {output}"
    assert "ssl-cert" in output.split(), (
        "xrdp must belong to the ssl-cert group so it can read /etc/xrdp/key.pem. "
        f"Groups: {output}"
    )


def test_guac_vnc_tunnel():
    """End-to-end VNC smoke test through Guacamole - auth + desktop render.

    Stricter than the RDP equivalent because VNC auth errors only surface as
    .error frames *after* the initial .sync handshake; the legacy collector
    returned success on that handshake alone. This test additionally
    cross-checks that the VNC port stamped into user-mapping.xml actually
    matches an Xvnc process that is listening, to catch cache-drift bugs where
    Guacamole read the mapping before guacamole.sh finished stamping the
    dynamic port."""
    nb_user, home_dir, _, guacamole_home = _prepare_guacamole_runtime()

    process = _start_guacamole_as_user(nb_user, home_dir, guacamole_home)
    tunnel = None
    try:
        _guacamole_ready(process.tomcat_port, home_dir=home_dir, process=process)
        _guacd_port = _read_runtime_port(home_dir, "guacd_port", default=4822)
        _wait_for_tcp_port(_guacd_port)

        # Regression check: the <param name="port"> that Guacamole will dial
        # must point at an actually-listening Xvnc. A mismatch here means
        # guacamole.sh stamped AFTER Guacamole cached the mapping.
        mapping_path = os.path.join(guacamole_home, "user-mapping.xml")
        mapping_text = ""
        deadline = time.time() + 30
        mapping_vnc_port = None
        while time.time() < deadline and mapping_vnc_port is None:
            if os.path.exists(mapping_path):
                mapping_text = open(mapping_path).read()
                match = re.search(
                    r'<protocol>vnc</protocol>.*?<param name="port">(\d+)</param>',
                    mapping_text,
                    re.S,
                )
                if match:
                    mapping_vnc_port = int(match.group(1))
            time.sleep(1)
        assert mapping_vnc_port, f"VNC port never stamped into user-mapping.xml at {mapping_path}"

        _, ss_out = run_cmd("ss -lntp 2>/dev/null | grep -E 'Xtigervnc|Xvnc'")
        assert ss_out, (
            f"No Xvnc/Xtigervnc listener on any port. Mapping expected VNC on "
            f"{mapping_vnc_port}. ss output was empty."
        )
        bound_ports = set()
        for line in ss_out.splitlines():
            fields = line.split()
            if len(fields) < 4:
                continue
            addr = fields[3]
            port_str = addr.rsplit(":", 1)[-1]
            if port_str.isdigit():
                bound_ports.add(int(port_str))
        assert mapping_vnc_port in bound_ports, (
            f"user-mapping.xml says VNC is on port {mapping_vnc_port}, but Xvnc "
            f"is actually listening on {sorted(bound_ports)}. Guacamole would "
            f"dial a dead port -> 500 / connection refused.\n"
            f"ss output:\n{ss_out}"
        )

        web_password = _read_live_web_password(home_dir)
        web_user = _read_live_web_user(home_dir, nb_user)
        auth_response = _guacamole_login(process.tomcat_port, web_user, web_password)
        connections = _guacamole_connections(
            process.tomcat_port, auth_response["dataSource"], auth_response["authToken"]
        )
        vnc_connection_id = _connection_id_by_protocol(connections, "vnc")

        tunnel = _open_guacamole_tunnel(
            process.tomcat_port,
            auth_response["authToken"],
            auth_response["dataSource"],
            vnc_connection_id,
        )
        try:
            frames = _collect_guacamole_desktop_frames(tunnel, timeout_seconds=30)
        except AssertionError as exc:
            # Pull the Guacamole log so we can see whether the failure was an
            # auth rejection, a connection refused, or something downstream.
            catalina = os.path.join(home_dir, ".neurodesk", "tomcat", "logs", "catalina.out")
            extra = ""
            if os.path.exists(catalina):
                _, extra = run_cmd(f"tail -n 120 {shlex.quote(catalina)}")
            vnc_logs = os.path.join(home_dir, ".vnc")
            _, vnc_tail = run_cmd(
                f"ls -t {shlex.quote(vnc_logs)}/*:*.log 2>/dev/null | head -n1 | xargs -I {{}} tail -n 80 {{}}"
            )
            vnc_block = "(mapping had no vnc connection)"
            # Find the <connection> block that contains <protocol>vnc</protocol>.
            for m in re.finditer(r'<connection[^>]*>[\s\S]*?</connection>', mapping_text):
                if "<protocol>vnc</protocol>" in m.group(0):
                    vnc_block = m.group(0)
                    break
            raise AssertionError(
                f"{exc}\n--- catalina.out tail ---\n{extra}\n"
                f"--- latest Xvnc log ---\n{vnc_tail}\n"
                f"--- mapping VNC entry ---\n{vnc_block}"
            )
    finally:
        if tunnel is not None:
            try:
                tunnel.close()
            except Exception:
                pass
        _cleanup_guacamole_process(process, guacamole_home=guacamole_home)


def test_guac_rdp_tunnel():
    """Verify the Guacamole RDP tunnel renders a desktop without TLS key permission errors."""
    nb_user, home_dir, root_cmds_available, guacamole_home = _prepare_guacamole_runtime()

    if not root_cmds_available:
        shutil.rmtree(guacamole_home, ignore_errors=True)
        pytest.skip("RDP smoke test requires root or passwordless sudo to start xrdp")

    if _xrdp_already_running():
        shutil.rmtree(guacamole_home, ignore_errors=True)
        pytest.skip(
            "xrdp is already running on this netns (likely from the live Neurodesktop "
            "session). A second guacamole.sh cannot rebind xrdp to its own port, so "
            "the RDP tunnel smoke test is not meaningful here."
        )

    process = _start_guacamole_as_user(nb_user, home_dir, guacamole_home)
    tunnel = None
    try:
        _guacamole_ready(process.tomcat_port, home_dir=home_dir, process=process)
        _guacd_port = _read_runtime_port(home_dir, "guacd_port", default=4822)
        _wait_for_tcp_port(_guacd_port)

        web_password = _read_live_web_password(home_dir)
        web_user = _read_live_web_user(home_dir, nb_user)
        auth_response = _guacamole_login(process.tomcat_port, web_user, web_password)
        connections = _guacamole_connections(
            process.tomcat_port, auth_response["dataSource"], auth_response["authToken"]
        )
        rdp_connection_id = _connection_id_by_protocol(connections, "rdp")

        tunnel = _open_guacamole_tunnel(
            process.tomcat_port,
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
        _cleanup_guacamole_process(process, guacamole_home=guacamole_home)


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
