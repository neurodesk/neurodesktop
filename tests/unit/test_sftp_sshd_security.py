import os
import subprocess

from testlib import resolve_source


ENSURE_SFTP_SSHD = resolve_source(
    "/opt/neurodesktop/ensure_sftp_sshd.sh", "config/ssh/ensure_sftp_sshd.sh"
)


def test_sftp_sshd_forces_loopback_for_restored_legacy_config(tmp_path):
    """A home restored from an older image must not expose sshd off-host."""
    home = tmp_path / "home"
    ssh_dir = home / ".ssh"
    hostkeys = ssh_dir / "hostkeys"
    hostkeys.mkdir(parents=True)
    (hostkeys / "ssh_host_ed25519_key").write_text("test key\n")
    (hostkeys / "ssh_host_rsa_key").write_text("test key\n")
    # Existing home configs are deliberately preserved by the startup script.
    # This mirrors the image's legacy default, which had no ListenAddress.
    (ssh_dir / "sshd_config").write_text(
        "Port 2222\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n"
    )

    runtime = tmp_path / "runtime"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    started = tmp_path / "sshd-started"
    arguments = tmp_path / "sshd-arguments"

    fake_sshd = bin_dir / "sshd"
    fake_sshd.write_text(
        "#!/bin/bash\n"
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
    }

    result = subprocess.run(
        ["bash", str(driver)], env=env, capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (runtime / "sftp_port").read_text() == "2222\n"
    assert "ListenAddress=127.0.0.1\n" in arguments.read_text()
