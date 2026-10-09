import os
import subprocess

from testlib import repo_path


WORKFLOW = repo_path(".github/workflows/jupyter_test_main.yml")


def test_fsl_workflow_keeps_output_delivered_during_socket_shutdown(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    websocat = bin_dir / "websocat"
    websocat.write_text(
        """#!/usr/bin/env bash
finish() {
    printf '%s\\n' '["stdout", "__FSLMATHS_VALID_"]' '["stdout", "OUTPUT__\\r\\n"]'
    printf '%s\\n' '["stdout", "__FSLMATHS_COMPLETE_DONE__\\r\\n"]'
    touch "$PROBE_FINISHED"
    exit 0
}
trap finish TERM
IFS= read -r payload
touch "$PROBE_READY"
while true; do /usr/bin/sleep 0.01; done
"""
    )
    websocat.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text(
        """#!/usr/bin/env bash
if [ "$1" -ge 30 ]; then
    exec /usr/bin/sleep 1
fi
for ((i = 0; i < 100; i++)); do
    if [ -e "$PROBE_READY" ]; then break; fi
    /usr/bin/sleep 0.01
done
exec /usr/bin/sleep 0.01
"""
    )
    sleep.chmod(0o755)
    workflow = WORKFLOW.read_text()
    block = workflow.split('echo "--- Test 5: FSLMaths Command ---"', 1)[1].split(
        "# Cleanup terminal", 1
    )[0]
    env = os.environ.copy()
    env.update(
        PATH=f"{bin_dir}:{env['PATH']}",
        PROBE_READY=str(tmp_path / "ready"),
        PROBE_FINISHED=str(tmp_path / "finished"),
        FSL_PROBE_ATTEMPTS="1",
        FSL_PROBE_DELAY="0",
        FSL_PROBE_HOLD_OPEN="120",
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            'FSL_MODULE_LOADED=true; FSL_TESTS_PASSED=1; TOTAL_FSL_TESTS=2; '
            'WS_URL=wss://example.test/terminal/1; ADMIN_TOKEN=test-secret\n' + block,
        ],
        cwd=WORKFLOW.parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "finished").exists(), "socket never wrote its final frames"
    assert "FSL Tests Summary: 2/2 passed" in result.stdout, result.stdout
