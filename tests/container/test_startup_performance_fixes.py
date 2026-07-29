"""Startup-time fixes that need the running image.

The rest of the startup-script coverage — ensure_ssh_keys.sh and the
before_notebook.sh OLLAMA guard — runs without a container and lives in
``tests/unit/test_startup_performance_fixes.py``. What is left here needs a real
xrdp service and the image's /opt/jovyan_defaults tree.
"""

import socket
import time

from testlib import resolve_source, run_cmd


def _ensure_rdp_backend():
    return resolve_source(
        "/opt/neurodesktop/ensure_rdp_backend.sh",
        "config/guacamole/ensure_rdp_backend.sh",
    )


def _restore_home_defaults():
    return resolve_source(
        "/opt/neurodesktop/restore_home_defaults.sh",
        "config/jupyter/restore_home_defaults.sh",
    )



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
            f"bash {_ensure_rdp_backend()}",
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
        f"bash {_restore_home_defaults()}", env={"HOME": str(home)}
    )
    assert code == 0, f"restore_home_defaults.sh failed: {output}"

    assert not (home / ".local/bin/claude").exists(), (
        "claude binary must not be copied at boot; /usr/local/sbin/claude "
        "links to the image-owned binary on first use"
    )
    # Other defaults must still be restored.
    assert (home / ".vnc/xstartup").is_file(), "other defaults were not restored"
    assert (home / ".codex/config.toml").is_file(), "other defaults were not restored"