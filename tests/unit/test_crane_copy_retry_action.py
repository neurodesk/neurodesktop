import os
import subprocess

import yaml

from testlib import repo_path


ACTION = repo_path(".github/actions/crane-copy-retry/action.yml")
READ_ONLY = (
    "Error: PUT https://quay.io/v2/neurodesk/neurodesktop/manifests/2026-09-14: "
    "DENIED: System is currently read-only. Pulls will succeed but all write "
    "operations are currently suspended."
)
AUTH_DENIED = (
    "Error: PUT https://quay.io/v2/neurodesk/neurodesktop/manifests/2026-09-14: "
    "UNAUTHORIZED: access to the requested resource is not authorized"
)


def _run_action(tmp_path, outputs, max_attempts="3"):
    """Run the action's shell body with a fake crane that prints one output per attempt."""
    script = yaml.safe_load(ACTION.read_text())["runs"]["steps"][0]["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for index, output in enumerate(outputs, start=1):
        (tmp_path / f"attempt-{index}").write_text(output)

    (bin_dir / "crane").write_text(
        """#!/usr/bin/env bash
count=$(( $(cat "$STATE_DIR/count" 2>/dev/null || echo 0) + 1 ))
echo "$count" > "$STATE_DIR/count"
output="$STATE_DIR/attempt-$count"
if [ -s "$output" ]; then
  cat "$output"
  exit 1
fi
exit 0
"""
    )
    (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n")
    for tool in ("crane", "sleep"):
        (bin_dir / tool).chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STATE_DIR": str(tmp_path),
        "SOURCE": "ghcr.io/neurodesk/neurodesktop/neurodesktop:2026-09-14",
        "DESTINATION": "quay.io/neurodesk/neurodesktop:2026-09-14",
        "MAX_ATTEMPTS": max_attempts,
        "ATTEMPT_TIMEOUT_SECONDS": "60",
    }
    result = subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True
    )
    attempts = int((tmp_path / "count").read_text())
    return result, attempts


def test_quay_read_only_response_is_retried_until_writes_resume(tmp_path):
    result, attempts = _run_action(tmp_path, [READ_ONLY, READ_ONLY, ""])

    assert result.returncode == 0, result.stdout + result.stderr
    assert attempts == 3
    assert "succeeded on attempt 3." in result.stdout


def test_persistent_quay_read_only_response_fails_after_max_attempts(tmp_path):
    result, attempts = _run_action(tmp_path, [READ_ONLY] * 3)

    assert result.returncode == 1
    assert attempts == 3
    assert "failed after 3 attempts." in result.stdout
    assert "authentication or authorization error" not in result.stdout


def test_genuine_denial_fails_immediately(tmp_path):
    result, attempts = _run_action(tmp_path, ["DENIED: requested access to the resource is denied"])

    assert result.returncode == 1
    assert attempts == 1
    assert "authentication or authorization error; not retrying." in result.stdout


def test_auth_failure_alongside_read_only_response_is_not_retried(tmp_path):
    result, attempts = _run_action(tmp_path, [f"{READ_ONLY}\n{AUTH_DENIED}"])

    assert result.returncode == 1
    assert attempts == 1
    assert "authentication or authorization error; not retrying." in result.stdout
