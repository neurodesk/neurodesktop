"""Startup-time fixes that need the running image.

The rest of the startup-script coverage — ensure_ssh_keys.sh and the
before_notebook.sh OLLAMA guard — runs without a container and lives in
``tests/unit/test_startup_performance_fixes.py``. What is left here needs a real
xrdp service and the image's /opt/jovyan_defaults tree.
"""

import os
from pathlib import Path
import pytest
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
    """Adopt the root-provisioned RDP port without probing past its listener."""
    credentials = Path(f"/run/neurodesktop/rdp/{os.getuid()}")
    if not (credentials / "port").exists():
        pytest.skip("RDP requires root startup provisioning")
    port = (credentials / "port").read_text().strip()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    # An unrelated cached listener must not override the root-provisioned port.
    (runtime_dir / "rdp_port").write_text("12345\n")
    start = time.time()
    code, output = run_cmd(
        f"bash {_ensure_rdp_backend()}",
        env={"NEURODESKTOP_RUNTIME_DIR": str(runtime_dir)},
    )
    elapsed = time.time() - start

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
        "claude binary must not be copied at boot; the selector uses the "
        "image fallback until the user installs an update"
    )
    # Other defaults must still be restored.
    assert (home / ".vnc/xstartup").is_file(), "other defaults were not restored"
    assert (home / ".codex/config.toml").is_file(), "other defaults were not restored"
