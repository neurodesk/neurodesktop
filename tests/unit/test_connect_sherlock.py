"""Regression tests for the Sherlock Neurodesktop launcher."""

from __future__ import annotations

import os
import shlex
import subprocess

from testlib import repo_path


SCRIPT = repo_path("scripts/connectSherlock.sh")


def run_bash(command: str, *, env: dict[str, str] | None = None):
    return subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_connect_sherlock_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", SCRIPT], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr


def test_connect_sherlock_keeps_image_server_extensions_enabled():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "--ServerApp.jpserver_extensions" not in source
    assert "NEURODESKTOP_DISABLE_JPSERVER_EXTENSIONS" not in source
    assert "'jupyter_server_fileid': False" not in source


def test_connect_sherlock_updates_from_neurodesktop_repository():
    source = SCRIPT.read_text(encoding="utf-8")

    assert (
        "https://raw.githubusercontent.com/neurodesk/neurodesktop/"
        "refs/heads/main/scripts/connectSherlock.sh"
    ) in source


def test_sherlock_tunnels_do_not_request_ttys_for_fixed_commands():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "ExitOnForwardFailure=yes -t" not in source
    assert 'ssh -S "$SSH_SOCKET" -o ExitOnForwardFailure=yes -T \\' in source
    assert (
        'ssh -S "$CTRL_SOCKET" -o ExitOnForwardFailure=yes -T -L '
        '"${TUNNEL_PORT}:localhost:${TUNNEL_PORT}"'
    ) in source
    assert (
        "ssh -o ExitOnForwardFailure=yes -T -L "
        "${TUNNEL_PORT}:localhost:${NOTEBOOK_PORT}"
    ) in source


def test_notebook_url_is_printed_in_a_separate_access_banner():
    notebook_url = "http://127.0.0.1:22527/lab?token=test-token"
    command = f"""
source {shlex.quote(str(SCRIPT))}
print_neurodesktop_access_banner {shlex.quote(notebook_url)}
"""

    result = run_bash(command)

    assert result.returncode == 0, result.stderr
    assert (
        "\n==========================================================================\n"
        " Neurodesktop access link\n"
        "\n"
        " Open this link in your browser:\n"
        "\n"
        f"     {notebook_url}\n"
        "\n"
        " Allow about 30 seconds for startup.\n"
        "==========================================================================\n"
    ) in result.stdout


def test_ctrl_c_cancels_the_foreground_slurm_job(tmp_path):
    ssh_calls = tmp_path / "ssh-calls"
    env = os.environ.copy()
    env["SSH_CALLS"] = str(ssh_calls)
    command = f"""
source {shlex.quote(str(SCRIPT))}
ssh() {{
    printf '%s\n' "$*" >> "$SSH_CALLS"
    case "$*" in
        *"squeue -u "*) printf '41135191\n' ;;
        *"squeue -j 41135191 "*) printf 'R\n' ;;
    esac
}}
run_foreground_neurodesk_session socket sherlock neurodesktop \
    bash -c 'kill -INT "$PPID"; exit 130' <<<'y'
printf 'status=%s\n' "$?"
"""

    result = run_bash(command, env=env)

    assert result.returncode == 0, result.stderr
    assert "Tunnel closed. Job 41135191 is still running" in result.stdout
    assert "Cancel the session now?" in result.stdout
    assert "Cancelled job 41135191" in result.stdout
    assert "status=130" in result.stdout
    calls = ssh_calls.read_text(encoding="utf-8")
    assert "squeue -u $USER --name=neurodesktop" in calls
    assert "scancel 41135191" in calls


def test_ctrl_c_keeps_the_foreground_slurm_job_by_default(tmp_path):
    ssh_calls = tmp_path / "ssh-calls"
    env = os.environ.copy()
    env["SSH_CALLS"] = str(ssh_calls)
    command = f"""
source {shlex.quote(str(SCRIPT))}
ssh() {{
    printf '%s\n' "$*" >> "$SSH_CALLS"
    case "$*" in
        *"squeue -u "*) printf '41135191\n' ;;
        *"squeue -j 41135191 "*) printf 'R\n' ;;
    esac
}}
run_foreground_neurodesk_session socket sherlock neurodesktop \
    bash -c 'kill -INT "$PPID"; exit 130' <<<''
printf 'status=%s\n' "$?"
"""

    result = run_bash(command, env=env)

    assert result.returncode == 0, result.stderr
    assert "Tunnel closed. Job 41135191 is still running" in result.stdout
    assert "Leaving job 41135191 running" in result.stdout
    assert "status=130" in result.stdout
    calls = ssh_calls.read_text(encoding="utf-8")
    assert "squeue -u $USER --name=neurodesktop" in calls
    assert "scancel 41135191" not in calls


def test_self_update_replaces_and_reexecutes_an_old_copy(tmp_path):
    old_script = tmp_path / "connectSherlock.sh"
    old_script.write_text("#!/bin/bash\necho old copy\n", encoding="utf-8")

    candidate = tmp_path / "candidate.sh"
    candidate.write_text(
        """#!/bin/bash
function connectSherlock() {
    printf 'updated copy ran with %s\\n' "$1"
}
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    connectSherlock "$@"
fi
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["CONNECT_SHERLOCK_TEST_CANDIDATE"] = str(candidate)
    command = f"""
source {shlex.quote(str(SCRIPT))}
download_connect_sherlock_update() {{ cp "$CONNECT_SHERLOCK_TEST_CANDIDATE" "$1"; }}
self_update_connect_sherlock {shlex.quote(str(old_script))} sentinel <<<'y'
"""
    result = run_bash(command, env=env)

    assert result.returncode == 0, result.stderr
    assert "Updated connectSherlock.sh" in result.stdout
    assert "updated copy ran with sentinel" in result.stdout
    assert old_script.read_text(encoding="utf-8") == candidate.read_text(
        encoding="utf-8"
    )


def test_self_update_keeps_the_current_copy_when_declined(tmp_path):
    old_script = tmp_path / "connectSherlock.sh"
    old_source = "#!/bin/bash\necho old copy\n"
    old_script.write_text(old_source, encoding="utf-8")

    candidate = tmp_path / "candidate.sh"
    candidate.write_text(
        """#!/bin/bash
function connectSherlock() {
    echo updated copy
}
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["CONNECT_SHERLOCK_TEST_CANDIDATE"] = str(candidate)
    command = f"""
source {shlex.quote(str(SCRIPT))}
download_connect_sherlock_update() {{ cp "$CONNECT_SHERLOCK_TEST_CANDIDATE" "$1"; }}
self_update_connect_sherlock {shlex.quote(str(old_script))} <<<'n'
"""

    result = run_bash(command, env=env)

    assert result.returncode == 0, result.stderr
    assert "Update connectSherlock.sh now? [Y/n]" in result.stdout
    assert "Continuing with the current version." in result.stdout
    assert old_script.read_text(encoding="utf-8") == old_source


def test_update_validation_rejects_invalid_bash(tmp_path):
    candidate = tmp_path / "candidate.sh"
    candidate.write_text(
        "#!/bin/bash\nfunction connectSherlock() {\n", encoding="utf-8"
    )
    command = (
        f"source {shlex.quote(str(SCRIPT))}; "
        f"validate_connect_sherlock_update {shlex.quote(str(candidate))}"
    )

    result = run_bash(command)

    assert result.returncode != 0
