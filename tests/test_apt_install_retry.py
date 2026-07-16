import os
import subprocess
from pathlib import Path


REPO_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/apt_install_retry.sh"
SCRIPT = REPO_SCRIPT if REPO_SCRIPT.exists() else Path("/usr/local/bin/apt-install-retry")


def test_apt_install_retry_preserves_the_failing_command_status():
    command = r"""
rm() { return 0; }
apt-get() { return 42; }
export -f rm apt-get
bash "$APT_INSTALL_RETRY_SCRIPT" curl
"""
    env = os.environ.copy()
    env["APT_INSTALL_RETRY_ATTEMPTS"] = "1"
    env["APT_INSTALL_RETRY_SCRIPT"] = str(SCRIPT)

    result = subprocess.run(
        ["bash", "-c", command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 42
    assert "apt-install-retry: failed after 1 attempts." in result.stderr
