"""Behavioral tests for the JupyterHub terminal-creation helper.

The helper is driven against a stubbed ``curl`` so the tests need no network and
no listening socket, and so each response the real single-user server can return
is reproducible exactly.
"""

import os
import subprocess

from testlib import repo_path


HELPER = repo_path(".github/workflows/create_jupyter_terminal.sh")

#: The body the play-eu user server returned in run 35292156768, which failed
#: the nightly probe in neurodesk/neurodesktop#932.
HUB_UNREACHABLE = (
    '{"message": "Failed to connect to Hub API at \'http://hub:8081/hub/api\'.  '
    "Is the Hub accessible at this URL (from host: jupyter-akshitbeniwal)?\", "
    '"reason": null}'
)

CURL_STUB = r"""#!/usr/bin/env bash
# Replay CURL_STUB_PLAN one line per call: <http-code>|<exit-status>|<body>.
# The last line repeats once the plan runs out.
calls=$(( $(cat "$CURL_STUB_CALLS") + 1 ))
printf '%s\n' "$calls" > "$CURL_STUB_CALLS"
printf '%s\n' "$*" >> "$CURL_STUB_ARGV"

output=""
previous=""
for argument in "$@"; do
    if [ "$previous" = "--output" ]; then
        output="$argument"
    fi
    previous="$argument"
done

total=$(wc -l < "$CURL_STUB_PLAN")
line=$calls
if [ "$line" -gt "$total" ]; then
    line=$total
fi
plan=$(sed -n "${line}p" "$CURL_STUB_PLAN")

printf '%s' "${plan#*|*|}" > "$output"
printf '%s' "${plan%%|*}"
rest=${plan#*|}
exit "${rest%%|*}"
"""


def _run(tmp_path, plan, token="secret-token", **env_overrides):
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    curl = stub_dir / "curl"
    curl.write_text(CURL_STUB)
    curl.chmod(0o755)

    plan_file = tmp_path / "plan"
    plan_file.write_text("".join(f"{line}\n" for line in plan))
    calls = tmp_path / "calls"
    calls.write_text("0\n")
    argv = tmp_path / "argv"
    argv.write_text("")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{stub_dir}:{env['PATH']}",
            "CURL_STUB_PLAN": str(plan_file),
            "CURL_STUB_CALLS": str(calls),
            "CURL_STUB_ARGV": str(argv),
            "JUPYTER_API_TOKEN": token,
            "TERMINAL_CREATE_DELAY": "0",
        }
    )
    env.update(env_overrides)

    result = subprocess.run(
        ["bash", str(HELPER), "https://play-europe.neurodesk.org", "akshitbeniwal"],
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )
    return result, int(calls.read_text()), argv.read_text()


def test_terminal_creation_recovers_from_a_hub_connectivity_error(tmp_path):
    result, calls, _ = _run(
        tmp_path,
        [
            f"500|0|{HUB_UNREACHABLE}",
            f"500|0|{HUB_UNREACHABLE}",
            '201|0|{"name": "1"}',
        ],
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "1\n"
    assert calls == 3
    assert "Failed to connect to Hub API" in result.stderr


def test_terminal_creation_posts_to_the_user_server_terminals_endpoint(tmp_path):
    _, _, argv = _run(tmp_path, ['201|0|{"name": "4"}'])

    assert "--request POST" in argv
    assert (
        "https://play-europe.neurodesk.org/user/akshitbeniwal/api/terminals" in argv
    )


def test_terminal_creation_stops_on_a_forbidden_response(tmp_path):
    result, calls, _ = _run(tmp_path, ['403|0|{"status": 403}'])

    assert result.returncode == 1
    assert result.stdout == ""
    assert calls == 1
    assert "HTTP 403 will not change on retry" in result.stderr


def test_terminal_creation_retries_a_success_status_carrying_no_name(tmp_path):
    result, calls, _ = _run(
        tmp_path, ["200|0|not json at all", '200|0|{"name": "7"}']
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "7\n"
    assert calls == 2
    assert "HTTP 200 without a terminal name" in result.stderr


def test_terminal_creation_retries_a_transport_failure(tmp_path):
    result, calls, _ = _run(tmp_path, ["000|7|", '201|0|{"name": "2"}'])

    assert result.returncode == 0, result.stderr
    assert result.stdout == "2\n"
    assert calls == 2
    assert "curl exit 7" in result.stderr


def test_terminal_creation_gives_up_after_the_attempt_budget(tmp_path):
    result, calls, _ = _run(
        tmp_path,
        [f"503|0|{HUB_UNREACHABLE}"],
        TERMINAL_CREATE_ATTEMPTS="3",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert calls == 3
    assert "no terminal after 3 attempts" in result.stderr


def test_terminal_creation_keeps_the_token_out_of_its_diagnostics(tmp_path):
    token = "s3cret-play-eu-token"
    result, _, argv = _run(
        tmp_path, [f"500|0|{HUB_UNREACHABLE}"], token=token,
        TERMINAL_CREATE_ATTEMPTS="1",
    )

    assert f"Authorization: token {token}" in argv
    assert token not in result.stdout
    assert token not in result.stderr


def test_terminal_creation_refuses_an_empty_token(tmp_path):
    result, calls, _ = _run(tmp_path, ['201|0|{"name": "1"}'], token="")

    assert result.returncode == 2
    assert calls == 0
    assert "JUPYTER_API_TOKEN is empty" in result.stderr


def test_terminal_creation_rejects_a_non_numeric_attempt_budget(tmp_path):
    result, calls, _ = _run(
        tmp_path, ['201|0|{"name": "1"}'], TERMINAL_CREATE_ATTEMPTS="lots"
    )

    assert result.returncode == 2
    assert calls == 0
    assert "must be non-negative integers" in result.stderr
