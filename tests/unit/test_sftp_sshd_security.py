import os
import subprocess

import pytest

from testlib import resolve_source


ENSURE_SFTP_SSHD = resolve_source(
    "/opt/neurodesktop/ensure_sftp_sshd.sh", "config/ssh/ensure_sftp_sshd.sh"
)


@pytest.mark.parametrize("stale_pid", [False, True], ids=["fresh", "reused-pid"])
@pytest.mark.parametrize(
    ("saved_listeners", "effective_listeners", "use_fallback", "disable_ssh"),
    [
        ("", "listenaddress 127.0.0.1:2222\n", False, None),
        ("ListenAddress 127.0.0.1\n", "listenaddress 127.0.0.1:2222\n", False, "false"),
        (
            "ListenAddress 0.0.0.0\n",
            "listenaddress 127.0.0.1:2222\nlistenaddress 0.0.0.0:2222\n",
            True,
            None,
        ),
        (
            "ListenAddress ::\n",
            "listenaddress 127.0.0.1:2222\nlistenaddress [::]:2222\n",
            True,
            None,
        ),
        (
            "Include sshd_config.d/*.conf\n",
            "listenaddress 127.0.0.1:2222\nlistenaddress 192.0.2.1:2222\n",
            True,
            None,
        ),
        ("", "", True, None),
        ("", "listenaddress 127.0.0.1:2222\n", False, "true"),
    ],
    ids=["legacy", "loopback", "ipv4-wildcard", "ipv6-wildcard", "include", "missing", "disabled"],
)
def test_sftp_sshd_obeys_startup_policy_for_restored_config(
    tmp_path, saved_listeners, effective_listeners, use_fallback, disable_ssh, stale_pid
):
    """Restore loopback-only SFTP or skip it entirely when SSH is disabled."""
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
    if stale_pid:
        (ssh_dir / "sshd_2222.pid").write_text(f"{os.getpid()}\n")

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "sftp_port").write_text("2299\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    started = tmp_path / "sshd-started"
    arguments = tmp_path / "sshd-arguments"
    shared_tmp = tmp_path / "shared-tmp"
    shared_tmp.mkdir()
    attack_target = tmp_path / "other-user-file"
    attack_target.write_text("private\n")

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
        'previous=""\n'
        'for argument in "$@"; do\n'
        '  if [ "$previous" = "-o" ] && [[ "$argument" == PidFile=* ]]; then\n'
        '    pid_file=${argument#PidFile=}\n'
        '  fi\n'
        '  previous=$argument\n'
        'done\n'
        'if [[ "$pid_file" == "$FAKE_SHARED_TMP"/* ]]; then\n'
        '  rm -f "$pid_file"\n'
        '  ln -s "$FAKE_ATTACK_TARGET" "$pid_file"\n'
        'fi\n'
        'printf "4242\\n" > "$pid_file"\n'
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
    # Isolate the simulated world-writable directory from the host while
    # preserving the production path's ownership and race model.
    script = script.replace("/tmp/sshd_", f"{shared_tmp}/sshd_")
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
        "FAKE_SHARED_TMP": str(shared_tmp),
        "FAKE_ATTACK_TARGET": str(attack_target),
    }
    env.pop("NEURODESKTOP_DISABLE_SSH", None)
    if disable_ssh is not None:
        env["NEURODESKTOP_DISABLE_SSH"] = disable_ssh

    result = subprocess.run(
        ["bash", str(driver)], env=env, capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0, result.stdout + result.stderr
    if disable_ssh == "true":
        assert "NEURODESKTOP_DISABLE_SSH=true: skipping SSH/SFTP service." in result.stdout
        assert not started.exists()
        assert not arguments.exists()
        assert not (runtime / "sftp_port").exists()
        return
    assert (runtime / "sftp_port").read_text() == "2222\n"
    assert attack_target.read_text() == "private\n"
    assert f"PidFile={ssh_dir / 'sshd_2222.pid'}\n" in arguments.read_text()
    assert (ssh_dir / "sshd_2222.pid").read_text() == "4242\n"
    assert ssh_dir.stat().st_mode & 0o777 == 0o700
    assert "ListenAddress=127.0.0.1\n" in arguments.read_text()
    selected_config = ssh_dir / ("sshd_config.fallback" if use_fallback else "sshd_config")
    assert f"-f\n{selected_config}\n" in arguments.read_text()
    assert (ssh_dir / "sshd_config").read_text() == saved_config
