"""Regression tests for the Sherlock Neurodesktop launcher."""

from __future__ import annotations

import os
import shlex
import subprocess

import pytest

from testlib import repo_path


SCRIPT = repo_path("scripts/connectSherlock.sh")


def run_bash(command: str, *, env: dict[str, str] | None = None):
    return subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_connect_sherlock_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", SCRIPT], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr


def test_sherlock_disables_container_ssh_in_the_apptainer_invocation(tmp_path):
    setup = SCRIPT.read_text(encoding="utf-8").split(
        "unset APPTAINERENV_HOME\n", 1
    )[1].split("\nEOF", 1)[0]
    arguments = tmp_path / "apptainer-arguments"
    command = f"""
apptainer() {{ printf '%s\\n' "$@" > {shlex.quote(str(arguments))}; }}
GROUP_HOME={shlex.quote(str(tmp_path))}
NEURODESKTOP_DISABLE_SSH=false
{setup}
"""

    result = run_bash(command)

    assert result.returncode == 0, result.stderr
    argv = arguments.read_text(encoding="utf-8").splitlines()
    assert argv[0] == "run"
    assert "start-notebook.py" in argv
    settings = [argv[index + 1] for index, argument in enumerate(argv) if argument == "--env"]
    assert "NEURODESKTOP_DISABLE_SSH=true" in settings
    assert "CVMFS_DISABLE=true" in settings
    assert "--ServerApp.ip=127.0.0.1" in argv


@pytest.mark.parametrize("allocation,expected_limits", [
    ({"SLURM_MEM_PER_NODE": "8192", "SLURM_CPUS_ON_NODE": "1"}, ("8589934592", "1")),
    ({"SLURM_MEM_PER_CPU": "4096", "SLURM_JOB_CPUS_PER_NODE": "2(x2)"}, ("8589934592", "2")),
    ({"SLURM_MEM_PER_CPU": "2048", "SLURM_CPUS_PER_TASK": "3",
      "SLURM_CONF": "/etc/slurm/config with 'quotes'.conf"}, ("6442450944", "3")),
])
def test_interactive_launch_preserves_slurm_limits_across_ssh(tmp_path, allocation, expected_limits):
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "memory.max").write_text("max\n")
    (cgroup / "cpu.max").write_text("max 100000\n")
    inherited = tmp_path / "inherited"
    job = tmp_path / ".neurodesk_job.sh"
    job.write_text(f'''#!/bin/bash
source {shlex.quote(str(repo_path("config/jupyter/detect_resource_limits.sh")))}
detect_resource_limits {shlex.quote(str(cgroup))}
env -0 > {shlex.quote(str(inherited))}
''')
    job.chmod(0o700)
    source = SCRIPT.read_text()
    launch = source[source.rindex('    run_foreground_neurodesk_session "$CTRL_SOCKET"'):].split("\n}", 1)[0]
    exports = "\n".join(f"export {key}={shlex.quote(value)}" for key, value in allocation.items())
    command = f'''
CTRL_SOCKET=probe LOGIN_NODE=login JOB_NAME=probe TUNNEL_PORT=32338 NOTEBOOK_PORT=32338
PARTITION=dev WALLTIME=02:00:00 CPUS=1 MEM=8G GPU_FLAG= ENABLE_GPU_CONTAINER=0 TUNNEL_TOKEN=probe-token
export PROBE_HOME={shlex.quote(str(tmp_path))}
run_foreground_neurodesk_session() {{ shift 3; "$@"; }}
ssh() {{
    local remote="${{!#}}"
    if [[ "$remote" == salloc* ]]; then
        bash -c "$remote"
    else
        env -i HOME="$PROBE_HOME" PATH="$PATH" bash -c "$remote"
    fi
}}
salloc() {{
    while [[ "$1" != bash ]]; do shift; done
    export SLURM_JOB_ID=12345 SLURM_NODELIST=compute-node
    {exports}
    "$@"
}}
export -f ssh salloc
{launch}
'''
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("SLURM_") and key not in {"MEM_LIMIT", "CPU_LIMIT"}}

    result = run_bash(command, env=env)

    assert result.returncode == 0, result.stderr
    values = dict(item.split("=", 1) for item in inherited.read_text().split("\0") if item)
    assert (values.get("MEM_LIMIT"), values.get("CPU_LIMIT")) == expected_limits
    assert {key: values.get(key) for key in allocation} == allocation


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
