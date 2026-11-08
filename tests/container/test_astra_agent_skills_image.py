"""Behavioral contract for the Lightcone skills across all three agents.

The registration commands in the Dockerfile only prove that a plugin is listed.
These tests prove the parts that actually break in use: that the hooks' `jq`
dependency is present, that the pinned image-owned `astra` wins on PATH, that
the skill teaches the same schema version `astra validate` speaks, and that
OpenCode -- which has no Lightcone marketplace client -- still finds every
skill and runs the upstream hooks from a restored home.
"""

import base64
from contextlib import contextmanager
import http.server
import importlib.metadata
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from testlib import run_cmd


ASTRA_TOOLS_VERSION = "0.2.18"

SKILLS_CHECKOUT = Path("/opt/neurodesktop/agent-skills")
HOOK_SCRIPTS = SKILLS_CHECKOUT / "plugins/reproduction/hooks/scripts"
OPENCODE_SKILLS = Path(
    "/opt/jovyan_defaults/.config/opencode/skills"
)
OPENCODE_HOOKS = Path(
    "/opt/jovyan_defaults/.config/opencode/plugins/lightcone-hooks.js"
)
BET_PROJECT = Path("/opt/neurodesktop/examples/astra-bet")

CODEX_ENV = {"HOME": "/opt/jovyan_defaults", "CODEX_HOME": "/opt/jovyan_defaults/.codex"}
CLAUDE_ENV = {"HOME": "/opt/jovyan_defaults", "DISABLE_TELEMETRY": "1"}


@contextmanager
def opencode_server(home, cwd):
    """Serve OpenCode 2 for ``home`` and yield a reader for its location API."""
    password = secrets.token_urlsafe(16)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("OPENCODE_", "XDG_"))},
        "HOME": str(home),
        "OPENCODE_PASSWORD": password,
        "OPENCODE_DISABLE_MODELS_FETCH": "true",
    }
    log = home / "opencode-serve.log"
    with log.open("w") as stream:
        server = subprocess.Popen(
            ["/usr/bin/opencode", "serve", "--hostname=127.0.0.1", f"--port={port}"],
            cwd=cwd, env=environment, stdout=stream, stderr=stream,
            start_new_session=True,
        )
    token = base64.b64encode(f"opencode:{password}".encode()).decode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path, directory):
        query = urllib.parse.urlencode({"location[directory]": str(directory)})
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}?{query}",
            headers={"Authorization": f"Basic {token}"},
        )
        with opener.open(request, timeout=30) as response:
            return json.load(response)["data"]

    try:
        deadline = time.monotonic() + 60
        while "server listening on" not in log.read_text():
            assert server.poll() is None, log.read_text()
            assert time.monotonic() < deadline, log.read_text()
            time.sleep(0.2)
        yield get
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(server.pid, signal.SIGKILL)
            server.wait(timeout=5)


@contextmanager
def scripted_model(tool_calls):
    """An OpenAI-compatible endpoint that plays ``tool_calls``, then answers.

    Requests without tools (title generation) get a plain reply, so the script
    advances only on agent turns. Every request body is recorded.
    """
    requests, pending = [], list(tool_calls)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if body.get("tools") and pending:
                name, arguments = pending.pop(0)
                delta = {"role": "assistant", "tool_calls": [{
                    "index": 0, "id": f"call_{len(requests)}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}
                finish = "tool_calls"
            else:
                delta, finish = {"role": "assistant", "content": "DONE"}, "stop"
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
            ]
            payload = "".join(
                "data: " + json.dumps({"id": "x", "object": "chat.completion.chunk",
                                       "created": 1, "model": "m", **chunk}) + "\n\n"
                for chunk in chunks
            ) + "data: [DONE]\n\n"
            data = payload.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def run_hook(script, payload, env=None):
    """Feed a hook script a Claude/Codex hook payload and return its output."""
    quoted = json.dumps(payload).replace("'", "'\\''")
    return run_cmd(
        f"printf '%s' '{quoted}' | bash {HOOK_SCRIPTS / script}",
        env=env,
        timeout=120,
    )


def test_jq_is_installed_for_the_astra_hooks():
    """Every ASTRA hook parses its payload with jq; without it they are no-ops."""
    code, output = run_cmd("command -v jq")
    assert code == 0, output

    code, output = run_cmd("printf '{\"a\":1}' | jq -r '.a'")
    assert code == 0, output
    assert output == "1"


def test_pinned_astra_cli_wins_with_one_image_owned_install():
    code, output = run_cmd("command -v astra")
    assert code == 0, output
    assert output == "/opt/conda/bin/astra"

    # Scanned in Python rather than with `command -v -a`, which is a bashism
    # that dash -- the image's /bin/sh -- does not accept.
    # Persistent deployments may contain a user-managed astra under HOME, and
    # launchers may repeat an otherwise identical PATH directory. Neither is a
    # second install shipped by this image. Check each unique image-owned PATH
    # directory while retaining command -v above as the shadowing guard.
    home = Path.home().resolve()
    seen_directories = set()
    on_path = []
    for directory in os.environ["PATH"].split(os.pathsep):
        resolved_directory = Path(directory).resolve()
        if resolved_directory in seen_directories:
            continue
        seen_directories.add(resolved_directory)
        if resolved_directory == home or home in resolved_directory.parents:
            continue
        candidate = resolved_directory / "astra"
        if candidate.exists():
            on_path.append(str(candidate))
    assert on_path == ["/opt/conda/bin/astra"], (
        f"a second image-owned astra install could drift from the viewer: {on_path}"
    )

    code, output = run_cmd("astra --version")
    assert code == 0, output
    assert output == f"astra, version {ASTRA_TOOLS_VERSION}"

    # uv remains installed: `lc` is deliberately kept in its own uv tool
    # environment so its Dask graph cannot perturb JupyterLab.
    code, output = run_cmd("uv --version")
    assert code == 0, output
    assert output.startswith("uv 0.13.7")

    code, output = run_cmd("UV_TOOL_DIR=/opt/uv/tools uv tool list")
    assert code == 0, output
    assert "lightcone-cli v0.5.0" in output
    assert "- lc" in output


def test_skill_toolchain_pins_match_the_installed_toolchain():
    """The skill must teach the schema that the installed `astra` validates."""
    pins = (HOOK_SCRIPTS / "astra-pins.sh").read_text(encoding="utf-8")
    tools_pin = re.search(r'ASTRA_TOOLS_PIN="([^"]+)"', pins).group(1)
    spec_pin = re.search(r'ASTRA_SPEC_PIN="([^"]+)"', pins).group(1)

    assert tools_pin == importlib.metadata.version("astra-tools"), (
        "the pinned agent-skills commit teaches a different astra-tools than "
        "the image installs; bump AGENT_SKILLS_REF and the image pins together"
    )
    assert spec_pin == importlib.metadata.version("astra-spec")


def test_reproduction_plugin_is_installed_and_enabled_for_codex_and_claude():
    code, output = run_cmd("codex plugin list", env=CODEX_ENV, timeout=30)
    assert code == 0, output
    assert "reproduction@lightcone-research" in output
    assert "installed, enabled" in output

    code, output = run_cmd(
        "/opt/jovyan_defaults/.local/bin/claude plugin list",
        env=CLAUDE_ENV,
        timeout=30,
    )
    assert code == 0, output
    assert "reproduction@lightcone-research" in output
    assert "enabled" in output


def test_codex_wrapper_preserves_plugin_registration_after_mcp_refresh(tmp_path):
    """The real Codex writer may place plugin tables inside our MCP markers."""
    home = tmp_path / "home"
    codex_home = home / ".codex"
    work = tmp_path / "work"
    codex_home.mkdir(parents=True)
    work.mkdir()
    config = codex_home / "config.toml"
    config.write_text("", encoding="utf-8")
    env = {
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "BR_MCP_TOKEN": "wrapper-regression-test",
    }

    code, output = run_cmd(
        "/usr/local/sbin/codex --version", cwd=work, env=env, timeout=30
    )
    assert code == 0, output
    code, output = run_cmd(
        f"/usr/bin/codex plugin marketplace add {SKILLS_CHECKOUT}",
        cwd=work,
        env=env,
        timeout=30,
    )
    assert code == 0, output
    code, output = run_cmd(
        "/usr/bin/codex plugin add reproduction@lightcone-research",
        cwd=work,
        env=env,
        timeout=30,
    )
    assert code == 0, output

    text = config.read_text(encoding="utf-8")
    assert (
        text.index("# BEGIN brain-researcher MCP")
        < text.index("[marketplaces.lightcone-research]")
        < text.index("# END brain-researcher MCP")
    ), "The regression fixture no longer matches the real Codex config writer"

    for _ in range(2):
        code, output = run_cmd(
            "/usr/local/sbin/codex plugin list", cwd=work, env=env, timeout=30
        )
        assert code == 0, output
        assert "reproduction@lightcone-research" in output
        assert "installed, enabled" in output


def test_reproduction_is_the_only_lightcone_plugin_installed_by_default():
    """The self-contained plugin avoids a second ASTRA registration."""
    installed = json.loads(
        Path(
            "/opt/jovyan_defaults/.claude/plugins/installed_plugins.json"
        ).read_text(encoding="utf-8")
    )

    names = json.dumps(installed)
    assert "reproduction" in names
    assert "astra@lightcone-research" not in names


def test_opencode_finds_every_reproduction_skill_in_a_restored_home(tmp_path):
    """OpenCode discovers the portable skills from HOME."""
    skill_names = {
        "astra",
        "assess-reproducibility",
        "reproduce",
        "figure-comparison",
    }
    for skill in skill_names:
        source = OPENCODE_SKILLS / skill / "SKILL.md"
        assert source.is_file()
        assert not source.is_symlink(), (
            "restore_home_defaults.sh copies regular files only (find -type f), "
            "so a symlink here would never reach a user's home"
        )

    home = tmp_path / "home"
    home.mkdir()
    code, output = run_cmd(
        "bash /opt/neurodesktop/restore_home_defaults.sh",
        env={"HOME": str(home)},
        timeout=300,
    )
    assert code == 0, output

    for skill in skill_names:
        restored = home / f".config/opencode/skills/{skill}/SKILL.md"
        assert restored.is_file(), output
        frontmatter = restored.read_text(encoding="utf-8").split("---")[1]
        assert re.search(rf"^name: {re.escape(skill)}$", frontmatter, re.MULTILINE)
        assert re.search(r"^description:", frontmatter, re.MULTILINE)
    restored_hooks = home / ".config/opencode/plugins/lightcone-hooks.js"
    assert restored_hooks.is_file(), output
    assert not restored_hooks.is_symlink()

    # Skill discovery does not need to initialize external plugins. In
    # particular, the Lightcone adapter runs its session-start hook while it
    # loads, which makes this read-only assertion depend on unrelated external
    # work. The adapter itself is exercised by the focused test below.
    # OpenCode also discovers skills from the working directory, so serve it
    # from an empty project. OpenCode 2 indexes skills in the background, so
    # the first listings can still be incomplete.
    shutil.rmtree(home / ".config/opencode/plugins")
    project = tmp_path / "project"
    project.mkdir()
    with opencode_server(home, project) as get:
        deadline = time.monotonic() + 60
        while True:
            discovered = {skill["id"] for skill in get("/api/skill", project)}
            if skill_names <= discovered or time.monotonic() > deadline:
                break
            time.sleep(0.5)
    assert skill_names <= discovered, discovered


def test_opencode_adapter_runs_the_upstream_astra_hooks(tmp_path):
    """An OpenCode 2 session must return hook context to the model.

    A scripted model reads the ASTRA spec twice and then rewrites it unchanged.
    The requests OpenCode sends back prove that the session-start context
    reached the system prompt, that the first read activated the skill once,
    and that the save ran validation.
    """
    assert OPENCODE_HOOKS.is_file()
    assert not OPENCODE_HOOKS.is_symlink()
    home = tmp_path / "home"
    home.mkdir()
    code, output = run_cmd(
        "bash /opt/neurodesktop/restore_home_defaults.sh",
        env={"HOME": str(home)},
        timeout=300,
    )
    assert code == 0, output
    restored_hooks = home / ".config/opencode/plugins/lightcone-hooks.js"
    assert restored_hooks.is_file(), output
    project = tmp_path / "project"
    shutil.copytree(BET_PROJECT, project)
    spec = project / "astra.yaml"
    script = [
        ("read", {"path": str(spec)}),
        ("read", {"path": str(spec)}),
        ("write", {"path": str(spec), "content": spec.read_text(encoding="utf-8")}),
    ]
    with scripted_model(script) as (endpoint, requests):
        inline = {
            "provider": {"scripted": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {"baseURL": endpoint},
                "models": {"m": {"name": "m"}},
            }},
            "model": "scripted/m",
        }
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith(("OPENCODE_", "XDG_"))},
            "HOME": str(home),
            "OPENCODE_CONFIG_CONTENT": json.dumps(inline),
            "OPENCODE_DISABLE_MODELS_FETCH": "true",
        }
        result = subprocess.run(
            ["/usr/bin/opencode", "run", "--standalone", "--auto", "-m", "scripted/m",
             "Check the ASTRA spec."],
            cwd=project, env=environment, capture_output=True, text=True, timeout=300,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    turns = [request for request in requests if request.get("tools")]
    assert len(turns) == 4, [request["messages"][-1] for request in requests]
    system = "\n".join(
        str(message["content"]) for message in turns[0]["messages"]
        if message["role"] == "system"
    )
    assert "ASTRA project" in system
    results = [
        json.dumps(message["content"]) for message in turns[-1]["messages"]
        if message["role"] == "tool"
    ]
    assert len(results) == 3, results
    first_read, second_read, written = results
    assert "astra skill" in first_read
    assert "astra skill" not in second_read
    assert "validation passed" in written


def test_validate_on_save_hook_reports_a_passing_project(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(BET_PROJECT, project)

    code, output = run_hook(
        "validate-on-save.sh",
        {"tool_input": {"file_path": str(project / "astra.yaml")}},
    )

    assert code == 0, output
    context = json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert "validation passed" in context
    assert "not validated" not in context, "the hook fell back to the uv path"


def test_validate_on_save_hook_surfaces_a_real_validation_failure(tmp_path):
    """A dangling insight reference is a semantic error, so `astra` exits 1.

    Note that a *version* mismatch is not usable here: upstream `astra
    validate` treats it as a warning and still exits 0. The viewer's adapter
    deliberately hard-fails it instead, which is why the two tiers disagree.
    """
    project = tmp_path / "project"
    shutil.copytree(BET_PROJECT, project)
    spec = project / "astra.yaml"
    spec.write_text(
        spec.read_text(encoding="utf-8").replace(
            "insights: [bet_threshold_affects_boundary]",
            "insights: [no_such_insight]",
            1,
        ),
        encoding="utf-8",
    )

    code, output = run_hook(
        "validate-on-save.sh", {"tool_input": {"file_path": str(spec)}}
    )

    assert code == 0, output
    context = json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert "validation FAILED" in context
    assert "INVALID_INSIGHT_REF" in context


def test_validate_on_save_hook_ignores_unrelated_files(tmp_path):
    unrelated = tmp_path / "notes.yaml"
    unrelated.write_text("a: 1\n", encoding="utf-8")

    code, output = run_hook(
        "validate-on-save.sh", {"tool_input": {"file_path": str(unrelated)}}
    )

    assert code == 0
    assert output == ""


def test_activate_on_read_hook_nudges_once_per_session(tmp_path):
    payload = {
        "tool_input": {"file_path": str(BET_PROJECT / "astra.yaml")},
        "session_id": "astra-skill-image-test",
    }
    env = {"TMPDIR": str(tmp_path)}

    code, first = run_hook("activate-on-read.sh", payload, env=env)
    assert code == 0, first
    context = json.loads(first)["hookSpecificOutput"]["additionalContext"]
    assert "astra skill" in context

    code, second = run_hook("activate-on-read.sh", payload, env=env)
    assert code == 0, second
    assert second == "", "the nudge must fire once per session, not per read"


def test_provenance_helper_runs_from_its_own_shebang(tmp_path):
    """The unit tier invokes it through the test interpreter, not `#!`.

    So only the image can prove that `/opt/conda/bin/python` exists, that the
    command is on PATH and executable, and that `check` finds the PyYAML it
    needs to read a specification.
    """
    code, output = run_cmd("command -v neurodesk-astra-provenance")
    assert code == 0, output
    assert output == "/usr/local/bin/neurodesk-astra-provenance"

    project = tmp_path / "project"
    shutil.copytree(BET_PROJECT, project)
    spec = project / "astra.yaml"
    specification = yaml.safe_load(spec.read_text(encoding="utf-8"))
    for declared_output in specification["outputs"]:
        declared_output["recipe"]["container"] = "neurodesk fsl/6.0.7.22"
    spec.write_text(
        yaml.safe_dump(specification, sort_keys=False),
        encoding="utf-8",
    )

    # Nothing has run, so every declared output is unrecorded.
    code, output = run_cmd(f"neurodesk-astra-provenance check {project}")
    assert code == 1, output
    assert "nothing recorded what ran" in output

    attempt = project / "bet_brain.nii.gz.tmp"
    attempt.write_text("artifact bytes", encoding="utf-8")
    code, output = run_cmd(
        f"neurodesk-astra-provenance publish {attempt} "
        f"{project / 'bet_brain.nii.gz'} --output-id bet_brain --tool python",
    )
    assert code == 0, output
    record = json.loads(
        (project / "bet_brain.nii.gz.prov.json").read_text(encoding="utf-8")
    )
    assert record["tool"]["versions"], record

    # The recorded Python version is not the FSL release the spec declares.
    # The other three outputs are still unrecorded, so assert on the wording
    # that only a disagreement produces.
    code, output = run_cmd(f"neurodesk-astra-provenance check {project}")
    assert code == 1, output
    assert "bet_brain: declared 'neurodesk fsl/6.0.7.22' but" in output
    assert "recorded versions" in output


def test_retained_plugin_has_no_obsolete_lightcone_execution_guidance():
    root = Path("/opt/neurodesktop/agent-skills/plugins/reproduction")
    bodies = [path.read_text() for path in root.rglob("*")
              if path.is_file() and path.suffix in {".md", ".sh"}]
    assert bodies
    for obsolete in ("lc run --universe", "lc run --jobs", "lc verify", ".lightcone-manifest.json"):
        assert not any(obsolete in body for body in bodies), obsolete
