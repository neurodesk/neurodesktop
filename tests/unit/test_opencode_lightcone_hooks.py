"""The OpenCode 2 plugin that runs the pinned Lightcone hooks.

The driver calls the plugin's ``setup`` with the context OpenCode 2 passes and
then fires the registered hooks with OpenCode 2's event shapes. Stub hook
scripts stand in for the reproduction checkout; the installed-image test in
``tests/container/test_astra_agent_skills_image.py`` runs the real hooks
through a real OpenCode session.
"""

import json
import shutil
import subprocess

import pytest

from testlib import repo_path


DRIVER = """import plugin from './lightcone-hooks.mjs';
const directory = process.argv[2];
const hooks = {};
await plugin.setup({
  location: { directory },
  session: { hook: async (name, callback) => { hooks[`session.${name}`] = callback; } },
  tool: { hook: async (name, callback) => { hooks[`tool.${name}`] = callback; } },
});
const context = { system: [{ type: 'text', text: 'base' }] };
await hooks['session.context'](context);
const events = JSON.parse(process.argv[3]);
for (const event of events) await hooks['tool.execute.after'](event);
console.log(JSON.stringify({ registered: Object.keys(hooks).sort(), system: context.system, events }));
"""

HOOK = """#!/bin/bash
payload=$(cat)
printf '%s\\n' "$payload" >> "{log}"
jq -n --arg context "{name}: $(jq -r '.tool_input.file_path // .cwd' <<<"$payload")" \\
  '{{hookSpecificOutput: {{additionalContext: $context}}}}'
"""


@pytest.fixture
def run_plugin(tmp_path, monkeypatch):
    if shutil.which("jq") is None:
        pytest.skip("jq is required by the stub hooks")
    scripts = tmp_path / "root/hooks/scripts"
    scripts.mkdir(parents=True)
    log = tmp_path / "payloads.jsonl"
    for name in ("astra-session-start.sh", "activate-on-read.sh", "validate-on-save.sh"):
        (scripts / name).write_text(HOOK.format(log=log, name=name))
    shutil.copy(
        repo_path("config/agents/opencode_lightcone_hooks.js"),
        tmp_path / "lightcone-hooks.mjs",
    )
    (tmp_path / "driver.mjs").write_text(DRIVER)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("NEURODESKTOP_LIGHTCONE_PLUGIN_ROOT", str(tmp_path / "root"))

    def run(events):
        result = subprocess.run(
            ["node", str(tmp_path / "driver.mjs"), str(project), json.dumps(events)],
            capture_output=True, text=True, timeout=60, check=True,
        )
        payloads = [json.loads(line) for line in log.read_text().splitlines()]
        return json.loads(result.stdout), payloads, project

    return run


def completed(tool, path, content):
    return {
        "tool": tool, "sessionID": "ses_1", "agent": "build", "messageID": "msg_1",
        "id": "call_1", "input": {"path": path}, "status": "completed",
        "result": {"content": content, "metadata": {"kept": True}},
    }


def test_session_context_reaches_every_model_request(run_plugin):
    result, payloads, project = run_plugin([])
    assert result["registered"] == ["session.context", "tool.execute.after"]
    assert result["system"] == [
        {"type": "text", "text": "base"},
        {"type": "text", "text": f"astra-session-start.sh: {project}"},
    ]
    assert payloads == [{"cwd": str(project)}]


def test_file_tools_append_hook_context_to_their_results(run_plugin):
    events = [
        completed("edit", "astra.yaml", "Edited"),
        completed("read", "/abs/notes.md", [{"type": "text", "text": "Read"}]),
        completed("write", "out.txt", None),
        completed("shell", "astra.yaml", "untouched"),
    ]
    result, payloads, project = run_plugin(events)
    edited, read, written, shell = result["events"]
    spec = str(project / "astra.yaml")
    assert edited["result"] == {
        "content": f"Edited\n\nvalidate-on-save.sh: {spec}",
        "metadata": {"kept": True},
    }
    assert read["result"]["content"] == [
        {"type": "text", "text": "Read"},
        {"type": "text", "text": "activate-on-read.sh: /abs/notes.md"},
    ]
    assert written["result"]["content"] == f"validate-on-save.sh: {project / 'out.txt'}"
    assert shell["result"]["content"] == "untouched"
    assert payloads[1] == {
        "cwd": str(project),
        "session_id": "ses_1",
        "tool_name": "edit",
        "tool_input": {"path": "astra.yaml", "file_path": spec},
        "tool_response": {"filePath": spec},
    }
    assert len(payloads) == 4, "only file tools run a hook"


def test_failed_tools_are_left_alone(run_plugin):
    failed = {
        "tool": "edit", "sessionID": "ses_1", "agent": "build", "messageID": "msg_1",
        "id": "call_1", "input": {"path": "astra.yaml"}, "status": "error",
        "error": {"message": "no match"},
    }
    result, _, _ = run_plugin([failed])
    assert result["events"] == [failed]
