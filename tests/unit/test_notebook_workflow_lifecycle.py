"""Behavioral tests for the notebook workflow's server lifecycle steps."""

import os
import subprocess

import yaml

from testlib import repo_path


WORKFLOW = repo_path(".github/workflows/notebook_(FSL_bet)_workflow.yml")


CURL_STUB = r"""#!/usr/bin/env bash
method=GET
output=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --request)
            method="$2"
            shift 2
            ;;
        --output)
            output="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

echo "$method" >> "$CURL_STUB_LOG"
if [ "${CURL_STUB_SCENARIO:-stopped}" = "unreachable" ]; then
    echo "curl: (28) Connection timed out" >&2
    exit 28
fi

printf '%s' '{"server":null,"pending":null}' > "$output"
printf '%s\n' 'http=200 remote_ip=192.0.2.1 curl_exit=0'
"""


def _step_script(name):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["test-notebooks"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == name)
    return script.replace("${{ matrix.server_name }}", "test-server")


def _run_step(tmp_path, name, **env_overrides):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "curl").write_text(CURL_STUB)
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)

    log = tmp_path / "curl.log"
    log.touch(exist_ok=True)
    github_env = tmp_path / "github_env"
    github_env.touch(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "CURL_STUB_LOG": str(log),
            "USER_TOKEN": "secret-token",
            "USER": "akshitbeniwal",
            "API_URL": "https://hub.example",
            "GITHUB_ENV": str(github_env),
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            **env_overrides,
        }
    )
    result = subprocess.run(
        ["bash", "-e", "-c", _step_script(name)],
        cwd=repo_path("."),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, log.read_text().splitlines(), github_env.read_text()


def test_unreachable_initial_state_does_not_trigger_cleanup_requests(tmp_path):
    start, start_calls, start_env = _run_step(
        tmp_path,
        "Start JupyterHub Server",
        CURL_STUB_SCENARIO="unreachable",
    )

    assert start.returncode == 1
    assert start_calls == ["GET", "GET", "GET"]
    assert "SERVER_START_DIAGNOSTIC=initial-state-request-failed" in start_env

    cleanup, cleanup_calls, _ = _run_step(
        tmp_path,
        "Stop JupyterHub Server",
        CURL_STUB_SCENARIO="unreachable",
        SERVER_CLEANUP_REQUIRED="false",
    )

    assert cleanup.returncode == 0, cleanup.stderr
    assert cleanup_calls == start_calls
    assert "Server cleanup not required" in cleanup.stdout
    assert "SERVER_CLEANUP_REQUIRED=false" in start_env


def test_required_cleanup_still_stops_and_verifies_the_server(tmp_path):
    cleanup, calls, github_env = _run_step(
        tmp_path,
        "Stop JupyterHub Server",
        SERVER_CLEANUP_REQUIRED="true",
    )

    assert cleanup.returncode == 0, cleanup.stderr
    assert calls == ["DELETE", "GET"]
    assert "SERVER_STOP_SUCCEEDED=true" in github_env


def test_observed_state_marks_later_server_mutation_for_cleanup(tmp_path):
    start, calls, github_env = _run_step(tmp_path, "Start JupyterHub Server")

    # The stopped model allows the POST, but never becomes ready. Even though
    # startup subsequently fails, cleanup must run because POST reached curl.
    assert start.returncode == 1
    assert calls[0:2] == ["GET", "POST"]
    assert "SERVER_CLEANUP_REQUIRED=true" in github_env
