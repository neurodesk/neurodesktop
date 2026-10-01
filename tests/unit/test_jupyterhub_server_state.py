"""Behavioral tests for the JupyterHub server-state helper and its workflow use.

The user models below follow the shape JupyterHub's REST API returns for
GET /hub/api/users/<user>, including its default ``json.dumps`` spacing.
"""

import json
import os
import subprocess

import pytest
import yaml

from testlib import repo_path


HELPER = repo_path(".github/workflows/jupyterhub_server_state.sh")
WORKFLOW = repo_path(".github/workflows/jupyter_test_main.yml")


def _model(server=None, pending=None, default=None):
    model = {
        "kind": "user",
        "name": "akshitbeniwal",
        "admin": False,
        "server": server,
        "pending": pending,
        "servers": {},
    }
    if default is not None:
        model["servers"][""] = {
            "name": "",
            "url": "/user/akshitbeniwal/",
            "progress_url": "/hub/api/users/akshitbeniwal/server/progress",
            **default,
        }
    return model


STOPPED = _model()
SPAWNING = _model(pending="spawn", default={"ready": False, "pending": "spawn"})
READY = _model(
    server="/user/akshitbeniwal/", default={"ready": True, "pending": None}
)
STOPPING = _model(pending="stop", default={"ready": False, "pending": "stop"})


def _state(body):
    return subprocess.run(
        ["bash", str(HELPER)], input=body, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "model, expected",
    [
        (STOPPED, "stopped"),
        (SPAWNING, "pending:spawn"),
        (READY, "ready"),
        (STOPPING, "pending:stop"),
        # Server reported by URL but not yet marked ready.
        (_model(default={"ready": False, "pending": None}), "starting"),
        # Tokens without read:servers see only the top-level fields.
        ({"name": "u", "server": "/user/u/", "pending": None}, "ready"),
        ({"name": "u", "server": None, "pending": "spawn"}, "pending:spawn"),
        ({"name": "u", "server": None, "pending": None}, "stopped"),
    ],
)
def test_user_model_reduces_to_default_server_state(model, expected):
    for body in (json.dumps(model), json.dumps(model, separators=(",", ":"))):
        result = _state(body)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    "body",
    [
        '{"status": 403, "message": "Forbidden"}',
        "<html>502 Bad Gateway</html>",
        "",
        "[]",
    ],
)
def test_non_user_model_reports_no_state(body):
    result = _state(body)
    assert result.returncode == 1
    assert result.stdout == ""


CURL_STUB = r"""#!/usr/bin/env bash
# DELETE and POST requests and status probes are recorded; GETs replay
# CURL_STUB_MODELS one line per call, repeating the last line.
for argument in "$@"; do
    case "$argument" in
        DELETE|POST)
            echo "$argument" >> "$CURL_STUB_LOG"
            exit 0
            ;;
        %{http_code})
            echo STATUS >> "$CURL_STUB_LOG"
            printf '%s' "${CURL_STUB_STATUS:-200}"
            exit 0
            ;;
    esac
done
echo GET >> "$CURL_STUB_LOG"
calls=$(grep -c GET "$CURL_STUB_LOG")
total=$(wc -l < "$CURL_STUB_MODELS")
[ "$calls" -gt "$total" ] && calls=$total
sed -n "${calls}p" "$CURL_STUB_MODELS"
"""


def _step_script(name):
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["test-jupyterhub"]["steps"]
    return next(step["run"] for step in steps if step.get("name") == name)


def _run_step(tmp_path, name, models, **env_overrides):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(CURL_STUB)
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    model_file = tmp_path / "models"
    model_file.write_text("".join(json.dumps(m) + "\n" for m in models))
    log = tmp_path / "log"
    log.write_text("")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "CURL_STUB_MODELS": str(model_file),
            "CURL_STUB_LOG": str(log),
            "ADMIN_TOKEN": "secret-token",
            "USER": "akshitbeniwal",
            "API_URL": "https://hub.example",
            "GITHUB_ENV": str(tmp_path / "github_env"),
            **env_overrides,
        }
    )
    result = subprocess.run(
        ["bash", "-e", "-c", _step_script(name)],
        cwd=repo_path("."),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, log.read_text().split()


def test_cleanup_stops_waiting_once_the_hub_reports_server_null(tmp_path):
    # Issue #990: the old loop waited for the "server" key to vanish, which the
    # Hub never does, so every run sat out the full 120 seconds.
    result, calls = _run_step(tmp_path, "Stop JupyterHub Server", [STOPPED])

    assert result.returncode == 0, result.stderr
    assert "✅ Server stopped successfully" in result.stdout
    assert "(10s elapsed)" not in result.stdout
    assert calls == ["DELETE", "GET"]


def test_cleanup_retries_stop_while_spawn_is_still_pending(tmp_path):
    # A timed-out start leaves the spawn pending, and the Hub refuses to stop a
    # spawning server, so the stop request has to be repeated.
    result, calls = _run_step(
        tmp_path, "Stop JupyterHub Server", [SPAWNING, STOPPING, STOPPED]
    )

    assert result.returncode == 0, result.stderr
    assert "✅ Server stopped successfully" in result.stdout
    assert calls == ["DELETE", "GET", "DELETE", "GET", "GET"]


def test_start_waits_through_pending_spawn_until_ready(tmp_path):
    result, calls = _run_step(
        tmp_path,
        "Start JupyterHub Server",
        [STOPPED, SPAWNING, SPAWNING, READY],
    )

    assert result.returncode == 0, result.stderr
    assert "Server is still starting... (20s elapsed)" in result.stdout
    assert "✅ Server started successfully (30s elapsed)" in result.stdout
    assert calls == ["STATUS", "GET", "POST", "GET", "GET", "GET"]


def test_start_restarts_a_running_server_before_spawning(tmp_path):
    result, calls = _run_step(
        tmp_path, "Start JupyterHub Server", [READY, STOPPED, SPAWNING, READY]
    )

    assert result.returncode == 0, result.stderr
    assert "✅ Server stopped successfully" in result.stdout
    assert calls == ["STATUS", "GET", "DELETE", "GET", "POST", "GET", "GET"]


def test_start_times_out_reporting_the_last_state(tmp_path):
    result, _ = _run_step(tmp_path, "Start JupyterHub Server", [STOPPED, SPAWNING])

    assert result.returncode == 1
    assert "Server failed to start within timeout (last state: pending:spawn)" in (
        result.stdout
    )


def test_start_rejects_an_invalid_token(tmp_path):
    result, calls = _run_step(
        tmp_path, "Start JupyterHub Server", [STOPPED], CURL_STUB_STATUS="403"
    )

    assert result.returncode == 1
    assert "403 Forbidden" in result.stdout
    assert calls == ["STATUS"]


def test_server_steps_use_the_state_helper_instead_of_json_substrings():
    for name in ("Start JupyterHub Server", "Stop JupyterHub Server"):
        script = _step_script(name)
        assert "bash .github/workflows/jupyterhub_server_state.sh || true" in script
        assert '\\"server\\":' not in script
        assert '\\"ready\\": true' not in script
