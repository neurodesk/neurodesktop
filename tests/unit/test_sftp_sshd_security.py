import os
import subprocess

import pytest

from testlib import resolve_source


ENSURE_SFTP_SSHD = resolve_source(
    "/opt/neurodesktop/ensure_sftp_sshd.sh", "config/ssh/ensure_sftp_sshd.sh"
)


@pytest.mark.parametrize(
    ("saved_listeners", "effective_listeners", "use_fallback"),
    [
        ("", "listenaddress 127.0.0.1:2222\n", False),
        ("ListenAddress 127.0.0.1\n", "listenaddress 127.0.0.1:2222\n", False),
        (
            "ListenAddress 0.0.0.0\n",
            "listenaddress 127.0.0.1:2222\nlistenaddress 0.0.0.0:2222\n",
            True,
        ),
        (
            "ListenAddress ::\n",
            "listenaddress 127.0.0.1:2222\nlistenaddress [::]:2222\n",
            True,
        ),
        (
            "Include sshd_config.d/*.conf\n",
            "listenaddress 127.0.0.1:2222\nlistenaddress 192.0.2.1:2222\n",
            True,
        ),
        ("", "", True),
    ],
    ids=["legacy", "loopback", "ipv4-wildcard", "ipv6-wildcard", "include", "missing"],
)
def test_sftp_sshd_forces_loopback_for_restored_config(
    tmp_path, saved_listeners, effective_listeners, use_fallback
):
    """Saved addresses, including included directives, must not expose SFTP."""
    home = tmp_path / "home"
    ssh_dir = home / ".ssh"
    hostkeys = ssh_dir / "hostkeys"
    hostkeys.mkdir(parents=True)
    (hostkeys / "ssh_host_ed25519_key").write_text("test key\n")
    (hostkeys / "ssh_host_rsa_key").write_text("test key\n")
    # Existing home configs are deliberately preserved by the startup script.
    saved_config = (
        "Port 2222\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n"
        + saved_listeners
    )
    (ssh_dir / "sshd_config").write_text(saved_config)

    runtime = tmp_path / "runtime"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    started = tmp_path / "sshd-started"
    arguments = tmp_path / "sshd-arguments"

    fake_sshd = bin_dir / "sshd"
    fake_sshd.write_text(
        "#!/bin/bash\n"
        "if [[ \" $* \" == *\" -T \"* ]]; then\n"
        '  printf "%s" "$FAKE_SSHD_EFFECTIVE_CONFIG"\n'
        "  exit 0\n"
        "fi\n"
        "if [[ \" $* \" == *\" -t \"* ]]; then exit 0; fi\n"
        'printf "%s\\n" "$@" > "$FAKE_SSHD_ARGUMENTS"\n'
        'if [[ \" $* \" != *\" -o ListenAddress=127.0.0.1 \"* ]]; then exit 64; fi\n'
        'touch "$FAKE_SSHD_STARTED"\n'
    )
    fake_sshd.chmod(0o755)

    (bin_dir / "sudo").write_text("#!/bin/bash\nexit 1\n")
    (bin_dir / "sudo").chmod(0o755)
    (bin_dir / "ss").write_text(
        "#!/bin/bash\n"
        'if [ -e "$FAKE_SSHD_STARTED" ]; then\n'
        "  printf 'State Recv-Q Send-Q Local Address:Port Peer Address:Port\\n'\n"
        "  printf 'LISTEN 0 128 127.0.0.1:2222 0.0.0.0:*\\n'\n"
        "fi\n"
    )
    (bin_dir / "ss").chmod(0o755)

    # Redirect only the unavailable image-owned executable. The test still
    # executes the complete startup script and observes its published state.
    script = ENSURE_SFTP_SSHD.read_text().replace("/usr/sbin/sshd", str(fake_sshd))
    driver = tmp_path / "ensure_sftp_sshd.sh"
    driver.write_text(script)
    driver.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "NEURODESKTOP_RUNTIME_DIR": str(runtime),
        "SFTP_SSH_PORT": "2222",
        "FAKE_SSHD_ARGUMENTS": str(arguments),
        "FAKE_SSHD_STARTED": str(started),
        "FAKE_SSHD_EFFECTIVE_CONFIG": effective_listeners,
    }

    result = subprocess.run(
        ["bash", str(driver)], env=env, capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (runtime / "sftp_port").read_text() == "2222\n"
    assert "ListenAddress=127.0.0.1\n" in arguments.read_text()
    selected_config = ssh_dir / ("sshd_config.fallback" if use_fallback else "sshd_config")
    assert f"-f\n{selected_config}\n" in arguments.read_text()
    assert (ssh_dir / "sshd_config").read_text() == saved_config
