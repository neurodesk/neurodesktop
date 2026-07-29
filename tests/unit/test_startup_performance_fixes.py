"""Tests for the startup-time fixes that run without the image.

Covers:
- ensure_ssh_keys.sh: pre-generated SSH keypairs for the Guacamole SFTP
  side-channel (valid, idempotent, safe under concurrent invocation).
- before_notebook.sh: the OLLAMA_HOST guard repoints an unreachable endpoint
  at 127.0.0.1 quickly instead of letting notebook_intelligence block Jupyter
  startup on it.

ensure_rdp_backend.sh needs a real xrdp service and restore_home_defaults.sh
needs /opt/jovyan_defaults, so both live in
tests/container/test_startup_performance_fixes.py.
"""

import http.server
import os
import subprocess
import threading
import time

from testlib import resolve_source

ENSURE_SSH_KEYS = resolve_source(
    "/opt/neurodesktop/ensure_ssh_keys.sh", "config/ssh/ensure_ssh_keys.sh"
)
BEFORE_NOTEBOOK = resolve_source(
    "/usr/local/bin/before-notebook.d/before_notebook.sh",
    "config/jupyter/before_notebook.sh",
)


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
            ["bash", str(ENSURE_SSH_KEYS)],
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
