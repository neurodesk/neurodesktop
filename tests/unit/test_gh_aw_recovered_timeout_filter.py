import json
import os
import shutil
import subprocess

from testlib import repo_path


WRAPPER = repo_path(".github/scripts/gh_aw_detect_agent_errors_wrapper.cjs")


RECOVERED_LOG = """\
[codex-harness] attempt 1: process exit event exitCode=1 signal=SIGKILL
[codex-harness] attempt 1: process closed exitCode=1 signal=SIGKILL duration=10m 50s hasOutput=true
[codex-harness] attempt 1: partial execution — will retry as fresh run (attempt 2/4)
[codex-harness] attempt 2: process exit event exitCode=0
[codex-harness] attempt 2: process closed exitCode=0 duration=7m 36s hasOutput=true
[codex-harness] success on attempt 2: totalDuration=18m 32s
[codex-harness] done: exitCode=0 totalDuration=18m 32s
"""


POISONED_TOOL_OUTPUT = json.dumps(
    {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "aggregated_output": """\
[codex-harness] attempt 1: process closed exitCode=1 signal=SIGKILL duration=10m 50s hasOutput=true
[codex-harness] all 3 retries exhausted — giving up (exitCode=1)
""",
        },
    }
) + """\
\n[codex-harness] attempt 1: process closed exitCode=0
[codex-harness] done: exitCode=0 totalDuration=12m 30s
"""


def _normalize(log):
    result = subprocess.run(
        ["node", str(WRAPPER), "--normalize"],
        input=log,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_recovered_attempt_signals_are_hidden_from_upstream_detector():
    normalized = _normalize(RECOVERED_LOG)

    assert "signal=SIGKILL" not in normalized
    assert normalized.count("recovered_termination=SIGKILL") == 2
    assert "success on attempt 2" in normalized
    assert "done: exitCode=0" in normalized


def test_unrecovered_final_signal_remains_a_timeout_candidate():
    log = """\
[codex-harness] attempt 1: process closed exitCode=1 signal=SIGKILL duration=10m 50s hasOutput=true
[codex-harness] all 3 retries exhausted — giving up (exitCode=1)
"""

    assert _normalize(log) == log


def test_timeout_fixture_inside_tool_output_is_not_a_timeout_candidate():
    normalized = _normalize(POISONED_TOOL_OUTPUT)

    assert "signal=SIGKILL" not in normalized
    assert normalized.count("transcript_termination=SIGKILL") == 1
    assert "[codex-harness] done: exitCode=0" in normalized


def test_signal_after_success_marker_remains_a_timeout_candidate():
    log = """\
[codex-harness] attempt 1: process closed exitCode=1 signal=SIGKILL
[codex-harness] success on attempt 2: totalDuration=18m 32s
[codex-harness] done: exitCode=0 totalDuration=18m 32s
[codex-harness] attempt 1: process closed exitCode=1 signal=SIGKILL
"""

    normalized = _normalize(log)

    assert normalized.count("recovered_termination=SIGKILL") == 1
    assert normalized.count("signal=SIGKILL") == 1


def test_wrapper_restores_original_transcript_after_detection(tmp_path):
    installed_wrapper = tmp_path / "detect_agent_errors.cjs"
    upstream_detector = tmp_path / "detect_agent_errors.upstream.cjs"
    stdio_log = tmp_path / "agent-stdio.log"

    shutil.copyfile(WRAPPER, installed_wrapper)
    upstream_detector.write_text(
        """
const fs = require("fs");
if (require.main === module) {
  const log = fs.readFileSync(process.env.GH_AW_AGENT_STDIO_LOG, "utf8");
  process.stdout.write(log.includes("signal=SIG") ? "timeout=true" : "timeout=false");
}
module.exports = { MODEL_NOT_SUPPORTED_PATTERN: /model not supported/i };
""".lstrip()
    )
    stdio_log.write_text(RECOVERED_LOG)

    result = subprocess.run(
        ["node", str(installed_wrapper)],
        env={**os.environ, "GH_AW_AGENT_STDIO_LOG": str(stdio_log)},
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == "timeout=false"
    assert stdio_log.read_text() == RECOVERED_LOG


def test_wrapper_preserves_upstream_module_exports(tmp_path):
    installed_wrapper = tmp_path / "detect_agent_errors.cjs"
    upstream_detector = tmp_path / "detect_agent_errors.upstream.cjs"

    shutil.copyfile(WRAPPER, installed_wrapper)
    upstream_detector.write_text(
        'module.exports = { MODEL_NOT_SUPPORTED_PATTERN: /model not supported/i };\n'
    )

    script = """
const detector = require(process.argv[1]);
process.stdout.write(JSON.stringify({
  type: typeof detector.MODEL_NOT_SUPPORTED_PATTERN,
  matches: detector.MODEL_NOT_SUPPORTED_PATTERN.test("Model not supported"),
}));
"""
    result = subprocess.run(
        ["node", "-e", script, str(installed_wrapper)],
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(result.stdout) == {"type": "object", "matches": True}
