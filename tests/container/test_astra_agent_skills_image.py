"""Behavioral contract for the Lightcone skills across all three agents.

The registration commands in the Dockerfile only prove that a plugin is listed.
These tests prove the parts that actually break in use: that the hooks' `jq`
dependency is present, that the pinned image-owned `astra` wins on PATH, that
the skill teaches the same schema version `astra validate` speaks, and that
OpenCode -- which has no Lightcone marketplace client -- still finds every
skill and runs the upstream hooks from a restored home.
"""

import importlib.metadata
import json
import os
import re
import shutil
from pathlib import Path

from testlib import run_cmd


ASTRA_TOOLS_VERSION = "0.2.11"

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
    # environment so its Dask/Snakemake graph cannot perturb JupyterLab.
    code, output = run_cmd("uv --version")
    assert code == 0, output
    assert output.startswith("uv 0.12.3")


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

    code, output = run_cmd(
        "/usr/bin/opencode debug skill", env={"HOME": str(home)}, timeout=30
    )
    assert code == 0, output
    discovered = {skill["name"] for skill in json.loads(output)}
    assert skill_names <= discovered


def test_opencode_adapter_runs_the_upstream_astra_hooks(tmp_path):
    """The adapter must return hook context to the model, not only run a script."""
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
    runner = tmp_path / "run-hooks.mjs"
    runner.write_text(
        f'''import {{ LightconeHooks }} from "file://{restored_hooks}"
const directory = process.argv[2]
const filePath = `${{directory}}/astra.yaml`
const hooks = await LightconeHooks({{ directory }})
const system = {{ system: [] }}
await hooks["experimental.chat.system.transform"]({{}}, system)
const edited = {{ output: "Edited", metadata: {{ filepath: filePath }} }}
await hooks["tool.execute.after"](
  {{ tool: "edit", sessionID: "image-edit", callID: "1", args: {{ filePath }} }},
  edited,
)
const read = {{ output: "Read", metadata: {{}} }}
await hooks["tool.execute.after"](
  {{ tool: "read", sessionID: "image-read", callID: "2", args: {{ filePath }} }},
  read,
)
const readAgain = {{ output: "Read again", metadata: {{}} }}
await hooks["tool.execute.after"](
  {{ tool: "read", sessionID: "image-read", callID: "3", args: {{ filePath }} }},
  readAgain,
)
console.log(JSON.stringify({{ system, edited, read, readAgain }}))
''',
        encoding="utf-8",
    )

    code, output = run_cmd(f"node {runner} {project}", timeout=120)
    assert code == 0, output
    result = json.loads(output)
    assert "ASTRA project" in "\n".join(result["system"]["system"])
    assert "validation passed" in result["edited"]["output"]
    assert "astra skill" in result["read"]["output"]
    assert result["readAgain"]["output"] == "Read again"


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
