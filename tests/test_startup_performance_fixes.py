"""Tests for the startup-time fixes.

Covers:
- ensure_ssh_keys.sh: pre-generated SSH keypairs for the Guacamole SFTP
  side-channel (valid, idempotent, safe under concurrent invocation).
- ensure_rdp_backend.sh: re-runs reuse the previously published RDP port
  instead of probing past their own xrdp and timing out.
- restore_home_defaults.sh: does not copy the ~230MB claude binary into the
  home directory (the /usr/local/sbin/claude wrapper links to the image copy).
- before_notebook.sh: the OLLAMA_HOST guard repoints an unreachable endpoint
  at 127.0.0.1 quickly instead of letting notebook_intelligence block Jupyter
  startup on it.
"""

import http.server
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

ENSURE_SSH_KEYS = "/opt/neurodesktop/ensure_ssh_keys.sh"
ENSURE_RDP_BACKEND = "/opt/neurodesktop/ensure_rdp_backend.sh"
RESTORE_HOME_DEFAULTS = "/opt/neurodesktop/restore_home_defaults.sh"
BEFORE_NOTEBOOK = "/usr/local/bin/before-notebook.d/before_notebook.sh"


def run_cmd(cmd, env=None, timeout=180):
    """Run a shell command with optional environment overrides."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    process = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=merged_env,
        timeout=timeout,
    )
    return process.returncode, process.stdout.strip()


def _public_key_of(private_key_path):
    code, output = run_cmd(f"ssh-keygen -y -f {private_key_path}")
    assert code == 0, f"ssh-keygen -y failed for {private_key_path}: {output}"
    return output


# ---------------------------------------------------------------------------
# ensure_ssh_keys.sh
# ---------------------------------------------------------------------------


def test_ensure_ssh_keys_generates_valid_keypairs(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    code, output = run_cmd(f"bash {ENSURE_SSH_KEYS}", env={"HOME": str(home)})
    assert code == 0, f"ensure_ssh_keys.sh failed: {output}"

    for name in ("guacamole_rsa", "id_rsa"):
        private_key = home / ".ssh" / name
        assert private_key.is_file(), f"{name} was not generated"
        assert (home / ".ssh" / f"{name}.pub").is_file(), f"{name}.pub missing"
        # A valid private key must yield its public half.
        _public_key_of(private_key)


def test_ensure_ssh_keys_is_idempotent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    code, output = run_cmd(f"bash {ENSURE_SSH_KEYS}", env={"HOME": str(home)})
    assert code == 0, output

    first_contents = {
        name: (home / ".ssh" / name).read_bytes()
        for name in ("guacamole_rsa", "id_rsa")
    }

    code, output = run_cmd(f"bash {ENSURE_SSH_KEYS}", env={"HOME": str(home)})
    assert code == 0, output

    for name, contents in first_contents.items():
        assert (home / ".ssh" / name).read_bytes() == contents, (
            f"{name} was regenerated on a second run"
        )


def test_ensure_ssh_keys_concurrent_invocations_produce_valid_keys(tmp_path):
    """Boot-time pre-generation may race a desktop click; flock must serialise."""
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)

    processes = [
        subprocess.Popen(
            ["bash", ENSURE_SSH_KEYS],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        for _ in range(2)
    ]
    for process in processes:
        output, _ = process.communicate(timeout=180)
        assert process.returncode == 0, f"concurrent run failed: {output}"

    for name in ("guacamole_rsa", "id_rsa"):
        private_key = home / ".ssh" / name
        derived_public = _public_key_of(private_key)
        stored_public = (home / ".ssh" / f"{name}.pub").read_text().strip()
        # The stored .pub must belong to the stored private key - a lost race
        # would leave halves from two different generations.
        assert stored_public.startswith(derived_public.split()[0])
        assert derived_public.split()[1] in stored_public, (
            f"{name}.pub does not match the private key"
        )


# ---------------------------------------------------------------------------
# ensure_rdp_backend.sh
# ---------------------------------------------------------------------------


def test_rdp_backend_reuses_published_listening_port(tmp_path):
    """A re-run must adopt the port a previous run published, not probe past it.

    Regression: with xrdp already listening on 3389, a second run picked 3390,
    waited the full timeout for a port xrdp never rebinds, and dropped RDP
    from the Guacamole mapping.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "rdp_port").write_text(f"{port}\n")

    try:
        start = time.time()
        code, output = run_cmd(
            f"bash {ENSURE_RDP_BACKEND}",
            env={"NEURODESKTOP_RUNTIME_DIR": str(runtime_dir), "NEURODESKTOP_RDP_PORT": ""},
        )
        elapsed = time.time() - start
    finally:
        listener.close()

    assert code == 0, f"ensure_rdp_backend.sh failed: {output}"
    assert (runtime_dir / "rdp_port").read_text().strip() == str(port), (
        "published rdp_port was not reused"
    )
    assert elapsed < 5, (
        f"reuse path took {elapsed:.1f}s; it must not fall into the "
        "wait-for-port timeout"
    )


# ---------------------------------------------------------------------------
# restore_home_defaults.sh
# ---------------------------------------------------------------------------


def test_restore_home_defaults_skips_claude_binary(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    code, output = run_cmd(
        f"bash {RESTORE_HOME_DEFAULTS}", env={"HOME": str(home)}
    )
    assert code == 0, f"restore_home_defaults.sh failed: {output}"

    assert not (home / ".local/bin/claude").exists(), (
        "claude binary must not be copied at boot; /usr/local/sbin/claude "
        "links to the image-owned binary on first use"
    )
    # Other defaults must still be restored.
    assert (home / ".vnc/xstartup").is_file(), "other defaults were not restored"
    assert (home / ".codex/config.toml").is_file(), "other defaults were not restored"


# ---------------------------------------------------------------------------
# before_notebook.sh OLLAMA_HOST guard
# ---------------------------------------------------------------------------


def _run_ollama_guard(tmp_path, ollama_host):
    """Extract and run just the OLLAMA_HOST guard block from before_notebook.sh."""
    guard_block = tmp_path / "guard_block.sh"
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "sed -n '/Guard against a black-holed OLLAMA_HOST/,/^fi$/p' "
        f"{BEFORE_NOTEBOOK} > {guard_block}\n"
        f"grep -q 'OLLAMA_HOST' {guard_block} || exit 90\n"
        f"source {guard_block}\n"
        'echo "RESULT_OLLAMA_HOST=${OLLAMA_HOST}"\n'
    )
    start = time.time()
    code, output = run_cmd(
        f"bash {driver}", env={"OLLAMA_HOST": ollama_host}, timeout=60
    )
    elapsed = time.time() - start
    assert code != 90, "OLLAMA_HOST guard block not found in before_notebook.sh"
    assert code == 0, output
    return output, elapsed


def test_ollama_guard_repoints_unreachable_host(tmp_path):
    # 10.255.255.1 is a black hole: packets are dropped, not refused, which is
    # exactly the case that blocked Jupyter startup for 60s+.
    output, elapsed = _run_ollama_guard(tmp_path, "http://10.255.255.1:11434")
    assert "RESULT_OLLAMA_HOST=http://127.0.0.1:11434" in output, output
    assert elapsed < 10, f"guard took {elapsed:.1f}s; must fail fast"


def test_ollama_guard_keeps_reachable_host(tmp_path):
    server = http.server.HTTPServer(
        ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Any HTTP response (even an error status) proves reachability.
        output, _ = _run_ollama_guard(tmp_path, f"http://127.0.0.1:{port}")
    finally:
        server.shutdown()
        server.server_close()

    assert f"RESULT_OLLAMA_HOST=http://127.0.0.1:{port}" in output, output
