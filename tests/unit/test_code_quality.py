"""Exercise checker discovery and failures through its subprocess interface."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from testlib import repo_path


LINT_HOME = "tools/javascript-lint"
ESLINT = f"{LINT_HOME}/node_modules/eslint/bin/eslint.js"
LINT_COMMAND = [
    ESLINT,
    "--config",
    f"{LINT_HOME}/eslint.config.mjs",
    "--max-warnings",
    "0",
]

@pytest.fixture
def quality_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(
        repo_path("scripts/check_quality.py"), root / "scripts/check_quality.py"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "extensions/neurodesk-launcher").mkdir(parents=True)
    stub_eslint = root / ESLINT
    stub_eslint.parent.mkdir(parents=True)
    stub_eslint.write_text("// Stub; the node stub records the lint command.\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "commands.jsonl"
    for name in (
        "ruff",
        "mypy",
        "shellcheck",
        "node",
        "npm",
        "shfmt",
        "actionlint",
        "docker",
    ):
        tool = tools / name
        tool.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "name = pathlib.Path(sys.argv[0]).name\n"
            "entry = {'tool': name, 'args': sys.argv[1:], 'cwd': os.getcwd()}\n"
            "if name == 'node' and '--input-type=module' in sys.argv:\n"
            "    entry['source'] = sys.stdin.read()\n"
            "with open(os.environ['QUALITY_TEST_LOG'], 'a') as stream:\n"
            "    stream.write(json.dumps(entry) + '\\n')\n"
            "sys.exit(7 if os.environ.get('QUALITY_TEST_FAIL') == name else 0)\n"
        )
        tool.chmod(0o755)
    env = dict(
        os.environ, PATH=f"{tools}:{os.environ['PATH']}", QUALITY_TEST_LOG=str(log)
    )
    return root, env, log


def check(quality_repo, gate, **env_updates):
    root, env, _ = quality_repo
    return subprocess.run(
        [sys.executable, str(root / "scripts/check_quality.py"), gate],
        cwd=root,
        env=dict(env, **env_updates),
        text=True,
        capture_output=True,
    )


def commands(quality_repo):
    return [json.loads(line) for line in quality_repo[2].read_text().splitlines()]


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_shell_checks_tracked_and_new_extensionless_scripts(quality_repo):
    root, _, _ = quality_repo
    write(root, "tracked.sh", "echo tracked\n")
    write(root, "config/agents/new-agent", "#!/usr/bin/env bash\necho new\n")
    write(root, "config/slurm/new.sbatch", "#!/bin/bash\necho job\n")
    write(root, "plain.txt", "echo text\n")
    write(root, ".gitignore", "ignored.sh\n")
    write(root, "ignored.sh", "echo ignored\n")
    subprocess.run(["git", "add", "tracked.sh"], cwd=root, check=True)
    assert check(quality_repo, "shell").returncode == 0
    assert commands(quality_repo)[0]["args"] == [
        "config/agents/new-agent",
        "config/slurm/new.sbatch",
        "tracked.sh",
    ]


def test_javascript_checks_modules_and_excludes_frozen_and_generated_files(
    quality_repo,
):
    root, _, _ = quality_repo
    write(root, "config/owned.js", "export const value = 1;\n")
    write(root, "tests/unit/case.cjs", "module.exports = 1;\n")
    write(root, "tests/unit/case.mjs", "export default 1;\n")
    for name in (
        "tests/fixtures/widget-manager/upstream.js",
        "extensions/neurodesk-launcher/lib/index.js",
        "extensions/neurodesk-launcher/neurodesk_launcher/labextension/index.js",
        "extensions/astra-viewer/node_modules/dependency/index.js",
    ):
        write(root, name, "deliberately invalid JavaScript\n")
    assert check(quality_repo, "javascript").returncode == 0
    entries = commands(quality_repo)
    assert len(entries) == 4
    assert entries[0]["source"] == "export const value = 1;\n"
    assert entries[1]["args"] == ["--check", "tests/unit/case.cjs"]
    assert entries[2]["source"] == "export default 1;\n"
    assert entries[3]["args"] == [
        *LINT_COMMAND,
        "config/owned.js",
        "tests/unit/case.cjs",
        "tests/unit/case.mjs",
    ]


def test_javascript_scope_discovers_new_owned_files_beside_excluded_names(
    quality_repo,
):
    root, _, _ = quality_repo
    owned = (
        "config/lib/helper.js",
        "config/vendor/adapter.mjs",
        "extensions/astra-viewer/labextension/owned.cjs",
        "tests/fixtures/other/case.js",
    )
    for name in owned:
        write(root, name, "export {};\n")
    assert check(quality_repo, "javascript").returncode == 0
    assert commands(quality_repo)[-1]["args"] == [*LINT_COMMAND, *sorted(owned)]


def test_javascript_gate_lints_even_when_syntax_check_fails(quality_repo):
    write(quality_repo[0], "owned.js", "export {};\n")
    result = check(quality_repo, "javascript", QUALITY_TEST_FAIL="node")
    assert result.returncode == 1
    assert [entry["args"][0] for entry in commands(quality_repo)] == [
        "--input-type=module",
        ESLINT,
    ]


def test_missing_eslint_fails_with_install_guidance(quality_repo):
    root, _, _ = quality_repo
    (root / ESLINT).unlink()
    write(root, "owned.js", "export {};\n")
    result = check(quality_repo, "javascript")
    assert result.returncode == 1
    assert f"npm ci --prefix {LINT_HOME}" in result.stderr


def test_python_failure_still_runs_every_check_and_fails(quality_repo):
    result = check(quality_repo, "python", QUALITY_TEST_FAIL="ruff")
    assert result.returncode == 1
    assert [entry["args"] for entry in commands(quality_repo)] == [
        ["check", "."],
        ["--config-file", "mypy.ini"],
        # The runner itself runs on host Python 3.10, not mypy.ini's 3.12.
        [
            "--config-file",
            "mypy.ini",
            "--python-version",
            "3.10",
            "scripts/check_quality.py",
        ],
    ]


def test_type_failure_fails_the_python_gate(quality_repo):
    result = check(quality_repo, "python", QUALITY_TEST_FAIL="mypy")
    assert result.returncode == 1
    assert [entry["tool"] for entry in commands(quality_repo)] == [
        "ruff",
        "mypy",
        "mypy",
    ]


def test_gate_error_is_reported_and_later_gates_still_run(quality_repo):
    # git ls-files fails, so file discovery raises inside two gates.
    result = check(quality_repo, "all", GIT_DIR=str(quality_repo[0] / "missing"))
    assert result.returncode == 1
    assert "shell failed:" in result.stderr
    assert "javascript failed:" in result.stderr
    assert commands(quality_repo)[-1]["args"] == ["buildx", "build", "--check", "."]


def test_frontend_runs_both_scripts_in_extension(quality_repo):
    result = check(quality_repo, "frontend", QUALITY_TEST_FAIL="npm")
    assert result.returncode == 1
    entries = commands(quality_repo)
    assert [entry["args"] for entry in entries] == [
        ["run", "typecheck"],
        ["run", "lint"],
    ]
    assert all(
        entry["cwd"] == str(quality_repo[0] / "extensions/neurodesk-launcher")
        for entry in entries
    )


def test_all_continues_after_failure_and_reports_failure(quality_repo):
    write(quality_repo[0], "owned.js", "const value = 1;\n")
    result = check(quality_repo, "all", QUALITY_TEST_FAIL="ruff")
    assert result.returncode == 1
    assert {entry["tool"] for entry in commands(quality_repo)} == {
        "ruff",
        "mypy",
        "shellcheck",
        "node",
        "npm",
        "shfmt",
        "docker",
        "actionlint",
    }
    assert commands(quality_repo)[-1]["args"] == ["buildx", "build", "--check", "."]


def test_missing_tools_fail_with_install_guidance(quality_repo):
    result = check(quality_repo, "python", PATH=str(quality_repo[0]))
    assert result.returncode == 1
    assert "Missing ruff" in result.stderr
    assert "Missing mypy" in result.stderr
    assert "scripts/install_quality_tools.sh" in result.stderr


def test_real_javascript_syntax_error_fails(quality_repo):
    root, env, _ = quality_repo
    node = shutil.which("node", path=os.environ["PATH"])
    assert node, "Install Node.js as documented in docs/testing.md"
    stub_node = Path(env["PATH"].split(os.pathsep)[0]) / "node"
    stub_node.unlink()
    stub_node.symlink_to(node)
    write(root, "owned.js", "export const valid = 1;\n")
    assert check(quality_repo, "javascript").returncode == 0
    write(root, "owned.js", "export const broken = ;\n")
    result = check(quality_repo, "javascript")
    assert result.returncode == 1
    assert "SyntaxError" in result.stderr


@pytest.fixture
def real_lint_repo(quality_repo):
    root, env, _ = quality_repo
    node = shutil.which("node", path=os.environ["PATH"])
    assert node, "Install Node.js as documented in docs/testing.md"
    installed = repo_path(LINT_HOME) / "node_modules"
    assert (installed / "eslint").is_dir(), (
        f"Run npm ci --prefix {LINT_HOME} as documented in docs/testing.md"
    )
    stub_node = Path(env["PATH"].split(os.pathsep)[0]) / "node"
    stub_node.unlink()
    stub_node.symlink_to(node)
    shutil.rmtree(root / LINT_HOME / "node_modules")
    (root / LINT_HOME / "node_modules").symlink_to(installed)
    shutil.copyfile(
        repo_path(f"{LINT_HOME}/eslint.config.mjs"),
        root / LINT_HOME / "eslint.config.mjs",
    )
    return root


def test_real_eslint_rejects_an_undefined_name(quality_repo, real_lint_repo):
    root = real_lint_repo
    write(root, "config/owned.js", "export const total = [1].length;\n")
    assert check(quality_repo, "javascript").returncode == 0
    write(root, "config/owned.js", "export const total = [1].length + totl;\n")
    result = check(quality_repo, "javascript")
    assert result.returncode == 1
    assert "'totl' is not defined" in result.stdout
    assert "no-undef" in result.stdout


def test_real_eslint_applies_runtime_globals_per_file(quality_repo, real_lint_repo):
    root = real_lint_repo
    adapter = "(() => { new URL(__T3_PREFIX__, location.href); })();\n"
    write(root, "extensions/t3-code-server/neurodesk_t3_code/web_adapter.js", adapter)
    write(root, "tests/unit/driver.cjs", "require('node:fs');\n")
    write(root, "config/firefox/syspref.js", 'pref("a.b", true, locked);\n')
    assert check(quality_repo, "javascript").returncode == 0

    # The same browser adapter is rejected outside its declared runtime, and
    # Node's CommonJS globals do not leak into an ES module.
    write(root, "config/unclassified.js", adapter)
    write(root, "tests/unit/driver.mjs", "require('node:fs');\n")
    result = check(quality_repo, "javascript")
    assert result.returncode == 1
    report = result.stdout.rpartition(ESLINT)[2].split("\n", 1)[1]
    assert "config/unclassified.js" in report
    assert "'__T3_PREFIX__' is not defined" in report
    assert "'location' is not defined" in report
    assert "tests/unit/driver.mjs" in report
    assert "'require' is not defined" in report
    assert "web_adapter.js" not in report
    assert "syspref.js" not in report


def test_real_eslint_leaves_frozen_upstream_excerpts_unlinted_and_unchanged(
    quality_repo, real_lint_repo
):
    root = real_lint_repo
    source = "var unusedUpstream = undefinedUpstreamName;\n"
    write(root, "tests/fixtures/widget-manager/upstream.js", source)
    write(root, "config/owned.js", "export {};\n")
    result = check(quality_repo, "javascript")
    assert result.returncode == 0, result.stdout
    assert "upstream.js" not in result.stdout
    fixture = root / "tests/fixtures/widget-manager/upstream.js"
    assert fixture.read_text() == source
