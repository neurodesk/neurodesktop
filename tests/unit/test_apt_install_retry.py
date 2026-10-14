import os
import subprocess

from testlib import repo_path, resolve_source


SCRIPT = resolve_source(
    "/usr/local/bin/apt-install-retry", "scripts/apt_install_retry.sh"
)


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


def test_build_installs_packages_before_restricted_apt_wrapper():
    dockerfile = repo_path("Dockerfile").read_text()
    wrapper_install = dockerfile.index("neurodesktop_apt.py /usr/local/bin/apt")
    assert "apt-install-retry " not in dockerfile[wrapper_install:], (
        "Build-time apt retries must precede the restricted runtime apt wrapper"
    )
