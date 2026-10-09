import os

import pytest
import subprocess
from pathlib import Path

from testlib import repo_path


JUPYTER_TEST_WORKFLOW = repo_path(".github/workflows/jupyter_test_main.yml")
FSL_TERMINAL_PROBE = repo_path(".github/workflows/run_fsl_terminal_probe.sh")
NOTEBOOK_TEST_WORKFLOW = repo_path(".github/workflows/notebook_(FSL_bet)_workflow.yml")
CODESPELL_WORKFLOW = repo_path(".github/workflows/codespell.yml")


def test_codespell_skips_vendored_cytoscape_bundle():
    workflow = CODESPELL_WORKFLOW.read_text()
    skip_line = next(
        line.strip() for line in workflow.splitlines() if line.strip().startswith("skip:")
    )
    skipped = {
        path.strip() for path in skip_line.removeprefix("skip:").split(",")
    }

    assert (
        "./extensions/astra-viewer/neurodesk_astra_view/static/vendor/"
        "cytoscape.min.js"
    ) in skipped


def _fsl_probe_command(workflow: str) -> str:
    for line in workflow.splitlines():
        stripped = line.strip()
        if stripped.startswith('CMD4="') and stripped.endswith('"'):
            return stripped.removeprefix('CMD4="').removesuffix('"')
    raise AssertionError("FSL probe command not found in JupyterHub workflow")


def _fslmaths_probe_command() -> str:
    probe = FSL_TERMINAL_PROBE.read_text()
    for line in probe.splitlines():
        stripped = line.strip()
        if stripped.startswith('CMD5="') and stripped.endswith('"'):
            result = subprocess.run(
                ["bash", "-c", f"{stripped}\nprintf '%s' \"$CMD5\""],
                capture_output=True,
                check=True,
                text=True,
            )
            return result.stdout
    raise AssertionError("FSLMaths probe command not found in JupyterHub workflow")


def test_jupyterhub_terminal_creation_goes_through_the_retrying_helper():
    workflow = JUPYTER_TEST_WORKFLOW.read_text()
    terminal_step = workflow.split("- name: Test Terminal and FSL Functionality", 1)[
        1
    ].split("- name: Stop JupyterHub Server", 1)[0]

    assert "bash .github/workflows/create_jupyter_terminal.sh" in terminal_step
    assert 'JUPYTER_API_TOKEN="$ADMIN_TOKEN"' in terminal_step
    # A single unchecked POST is what reported 0/5 in issue #932.
    assert "-X POST" not in terminal_step
    assert 'TERMINAL_CREATE_STATUS=$?' in terminal_step
    assert '[ "$TERMINAL_CREATE_STATUS" -ne 0 ]' in terminal_step


def test_jupyterhub_fsl_module_load_requires_fslmaths_on_path():
    workflow = JUPYTER_TEST_WORKFLOW.read_text()

    assert "if [ ${#ML_OUT} -ge 0 ]" not in workflow
    assert (
        "source /opt/neurodesktop/environment_variables.sh >/dev/null 2>&1 "
        "&& ml fsl"
    ) in workflow
    assert "ml fsl && command -v fslmaths" in workflow
    assert "__FSL_MODULE_READY_${attempt}__" in workflow
    assert "echo ${FSL_READY_MARKER}" not in workflow
    assert "echo '__FSL_MODULE_READY_'${attempt}'__'" in workflow
    assert r"""(printf '%s\n' "[\"stdin\", \"$CMD4\\r\\n\"]" && sleep 25)""" in workflow
    assert 'grep -Fq "$FSL_READY_MARKER"' in workflow
    assert "FSL module loaded and fslmaths is on PATH" in workflow


def test_jupyterhub_fsl_probe_emits_marker_only_after_tool_is_found(tmp_path):
    workflow = JUPYTER_TEST_WORKFLOW.read_text()
    command = _fsl_probe_command(workflow)
    tool = tmp_path / "fslmaths"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    refresh = tmp_path / "environment_variables.sh"
    refresh.write_text(
        f'ml() {{ return 0; }}\nexport PATH="{tmp_path}:$PATH"\n'
    )
    command = command.replace(
        "/opt/neurodesktop/environment_variables.sh", str(refresh)
    )

    result = subprocess.run(
        ["bash", "-c", f"attempt=positive\n{command}"],
        capture_output=True,
        check=False,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 0
    assert str(tool) in result.stdout
    assert "__FSL_MODULE_READY_positive__" in result.stdout


def test_jupyterhub_fsl_probe_rejects_missing_module():
    workflow = JUPYTER_TEST_WORKFLOW.read_text()
    command = _fsl_probe_command(workflow)
    command = command.replace(
        "/opt/neurodesktop/environment_variables.sh", "/dev/null"
    )
    missing_module_command = command.replace(
        "ml fsl && command -v fslmaths",
        "module load funny-name-tool && command -v funny-name-tool",
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            f"module() {{ return 1; }}\nattempt=negative\n{missing_module_command}",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "__FSL_MODULE_READY_negative__" not in result.stdout


def test_jupyterhub_fslmaths_test_is_skipped_when_module_load_fails():
    workflow = JUPYTER_TEST_WORKFLOW.read_text()

    assert "FSL_MODULE_LOADED=false" in workflow
    assert 'if [ "$FSL_MODULE_LOADED" = true ]; then' in workflow
    assert "Skipping FSLMaths command because FSL module loading failed" in workflow


def _write_fake_tool(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def test_jupyterhub_fslmaths_probe_runs_a_real_image_operation(tmp_path):
    command = _fslmaths_probe_command()
    assert "__FSLMATHS_VALID_OUTPUT__" not in command
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    _write_fake_tool(
        tmp_path / "python",
        'for last; do :; done\nprintf "input" > "$last"',
    )
    _write_fake_tool(
        tmp_path / "fslmaths",
        'for last; do :; done\nprintf "output" > "$last"',
    )

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        check=False,
        text=True,
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "TMPDIR": str(scratch),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "__FSLMATHS_VALID_OUTPUT__" in result.stdout
    assert "__FSLMATHS_COMPLETE_DONE__" in result.stdout
    assert not list(scratch.iterdir()), "FSL probe left its temporary directory behind"


def test_jupyterhub_fslmaths_probe_rejects_failed_operation(tmp_path):
    command = _fslmaths_probe_command()
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    _write_fake_tool(
        tmp_path / "python",
        'for last; do :; done\nprintf "input" > "$last"',
    )
    _write_fake_tool(tmp_path / "fslmaths", "exit 1")

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        check=False,
        text=True,
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "TMPDIR": str(scratch),
        },
    )

    assert result.returncode != 0
    assert "__FSLMATHS_VALID_OUTPUT__" not in result.stdout
    assert "__FSLMATHS_FAILED_1__" in result.stdout
    assert "__FSLMATHS_COMPLETE_DONE__" in result.stdout
    assert not list(scratch.iterdir()), "failed FSL probe left temporary files behind"


def test_jupyterhub_fslmaths_output_is_captured_from_the_original_websocket():
    workflow = JUPYTER_TEST_WORKFLOW.read_text()
    probe = FSL_TERMINAL_PROBE.read_text()

    assert "bash .github/workflows/run_fsl_terminal_probe.sh" in workflow
    assert "FSL_STDIN_PAYLOAD=$(jq -cn --arg data" in probe
    assert '> "$WEBSOCKET_LOG" 2>&1 &' in probe
    assert 'FSL_OUTPUT=$(terminal_output)' in probe
    assert 'grep -Fq "$FSL_RUN_MARKER"' in probe
    assert 'grep -q "Usage: fslmaths"' not in workflow


FAKE_WEBSOCAT = r"""#!/usr/bin/env bash
printf '%s\n' "$*" > "$FAKE_WEBSOCAT_ARGV"
printf '%s\n' "$$" > "$FAKE_WEBSOCAT_PID"
IFS= read -r payload
printf '%s\n' "$payload" > "$FAKE_WEBSOCAT_STDIN"

case "$FAKE_WEBSOCAT_RESULT" in
    final-on-stop)
        final_frames() {
            printf '%s\n' '["stdout", "__FSLMATHS_VALID_"]'
            printf '%s\n' '["stdout", "OUTPUT__\r\n__FSLMATHS_COMPLETE_DONE__\r\n"]'
            exit 0
        }
        trap final_frames TERM
        while :; do read -r -t 0.1 ignored || :; done
        ;;
    timeout)
        exec /bin/sleep 30
        ;;
    incomplete)
        printf '%s\n' '["stdout", "__FSLMATHS_VALID_OUTPUT__\r\n"]'
        ;;
    diagnostic-only)
        printf '%s\n' '__FSLMATHS_VALID_OUTPUT__ __FSLMATHS_COMPLETE_DONE__'
        ;;
    success)
        printf '%s\n' '["stdout", "__FSLMATHS_VALID_"]'
        printf '%s\n' '["stdout", "OUTPUT__\r\n"]'
        printf '%s\n' '["stdout", "__FSLMATHS_COMPLETE_"]'
        printf '%s\n' '["stdout", "DONE__\r\n"]'
        ;;
    failure)
        printf '%s\n' '["stdout", "__FSLMATHS_FAILED_17__\r\n"]'
        printf '%s\n' '["stdout", "__FSLMATHS_COMPLETE_DONE__\r\n"]'
        ;;
    echo-only)
        data=$(printf '%s\n' "$payload" | jq -r '.[1]')
        jq -cn --arg data "$data" '["stdout", $data]'
        ;;
esac
"""


def _run_fsl_terminal_probe(tmp_path, result):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    websocat = bin_dir / "websocat"
    websocat.write_text(FAKE_WEBSOCAT)
    websocat.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text(
        '#!/bin/sh\n'
        'if [ "$1" = 30 ]; then echo "$$" > "$FAKE_INPUT_PID"; fi\n'
        'exec /bin/sleep "$@"\n'
    )
    sleep.chmod(0o755)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    argv = tmp_path / "argv"
    stdin = tmp_path / "stdin"

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "JUPYTER_API_TOKEN": "terminal-secret",
            "FSL_PROBE_ATTEMPTS": "2",
            "FSL_PROBE_DELAY": "1",
            # A long writer lifetime proves the helper reaps it when the
            # WebSocket exits instead of waiting for the hold-open timeout.
            "FSL_PROBE_HOLD_OPEN": "30",
            "TMPDIR": str(scratch),
            "FAKE_WEBSOCAT_PID": str(tmp_path / "websocket.pid"),
            "FAKE_INPUT_PID": str(tmp_path / "input.pid"),
            "FAKE_WEBSOCAT_ARGV": str(argv),
            "FAKE_WEBSOCAT_STDIN": str(stdin),
            "FAKE_WEBSOCAT_RESULT": result,
        }
    )
    completed = subprocess.run(
        ["bash", str(FSL_TERMINAL_PROBE), "wss://example.test/terminal/1"],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
        env=env,
    )
    assert not list(scratch.iterdir()), "probe left temporary files behind"
    for name in ("websocket.pid", "input.pid"):
        pid = int((tmp_path / name).read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    return completed, argv.read_text(), stdin.read_text()


@pytest.mark.parametrize("scenario", ["success", "final-on-stop"])
def test_fsl_terminal_probe_accepts_final_split_frames(tmp_path, scenario):
    result, argv, payload = _run_fsl_terminal_probe(tmp_path, scenario)

    assert result.returncode == 0, result.stderr
    assert "__FSLMATHS_VALID_OUTPUT__" in result.stdout
    assert "__FSLMATHS_COMPLETE_DONE__" in result.stdout
    assert "--text wss://example.test/terminal/1" in argv
    assert "Authorization: token terminal-secret" in argv
    assert "__FSLMATHS_VALID_OUTPUT__" not in payload
    assert "__FSLMATHS_COMPLETE_DONE__" not in payload


def test_fsl_terminal_probe_reports_remote_operation_failure(tmp_path):
    result, _, _ = _run_fsl_terminal_probe(tmp_path, "failure")

    assert result.returncode == 1
    assert "__FSLMATHS_FAILED_17__" in result.stdout
    assert "completed unsuccessfully" in result.stderr


def test_fsl_terminal_probe_rejects_echoed_command_as_completion(tmp_path):
    result, _, payload = _run_fsl_terminal_probe(tmp_path, "echo-only")

    assert result.returncode == 1
    assert "echo '__FSLMATHS_VALID_'OUTPUT'__'" in payload
    assert "command did not report completion" in result.stderr


@pytest.mark.parametrize("scenario", ["timeout", "incomplete", "diagnostic-only"])
def test_fsl_terminal_probe_rejects_missing_completion_and_cleans_up(tmp_path, scenario):
    result, _, _ = _run_fsl_terminal_probe(tmp_path, scenario)
    assert result.returncode == 1
    assert "command did not report completion" in result.stderr


def test_notebook_server_start_reports_transport_failures_and_reconciles_retries():
    workflow = NOTEBOOK_TEST_WORKFLOW.read_text()
    start_step = workflow.split("- name: Start JupyterHub Server", 1)[1].split(
        "- name: Create and Execute FSL Notebook", 1
    )[0]

    assert "--silent --show-error --fail-with-body" in start_step
    assert "--connect-timeout 15" in start_step
    assert "--max-time 60" in start_step
    assert "remote_ip=%{remote_ip}" in start_step
    assert "tcp=%{time_connect}" in start_step
    assert "tls=%{time_appconnect}" in start_step
    assert "first_byte=%{time_starttransfer}" in start_step
    assert "curl_exit=%{exitcode}" in start_step
    assert "--retry-all-errors" not in start_step
    # Reconciliation must actually gate the retry flow, not merely be defined.
    assert "if reconcile_server_state; then" in start_step
    assert 'set_start_diagnostic "initial-state-request-failed"' in start_step
    assert 'set_start_diagnostic "existing-server-stop-timeout"' in start_step
    assert 'set_start_diagnostic "spawn-failed-state-unknown"' in start_step
    assert 'set_start_diagnostic "spawn-request-failed"' in start_step
    assert 'set_start_diagnostic "server-readiness-timeout"' in start_step
    assert 'SERVER_START_SUCCEEDED=false' in start_step
    assert 'SERVER_START_SUCCEEDED=true' in start_step
    assert 'SERVER_START_DIAGNOSTIC=not-attempted' in start_step


def test_notebook_workflow_packages_the_authenticated_acceptance_cli():
    workflow = NOTEBOOK_TEST_WORKFLOW.read_text()
    assert 'python scripts/check_fsl_notebook.py' in workflow
    assert '--server-url "$API_URL/user/$USER/"' in workflow
    assert 'path: fsl-evidence/' in workflow
    assert 'test "${SERVER_STOP_SUCCEEDED:-false}" = "true"' in workflow
    assert 'test "${NOTEBOOK_SUCCESS:-false}" = "true"' in workflow
