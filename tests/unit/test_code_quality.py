"""Exercise checker discovery and failures through its subprocess interface."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from testlib import repo_path


@pytest.fixture
def quality_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copyfile(
        repo_path("scripts/check_quality.py"), root / "scripts/check_quality.py"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "extensions/neurodesk-launcher").mkdir(parents=True)
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
        "extensions/astra-viewer/vendor/upstream.js",
        "extensions/neurodesk-launcher/neurodesk_launcher/labextension/index.js",
    ):
        write(root, name, "deliberately invalid JavaScript\n")
    assert check(quality_repo, "javascript").returncode == 0
    entries = commands(quality_repo)
    assert len(entries) == 3
    assert entries[0]["source"] == "export const value = 1;\n"
    assert entries[1]["args"] == ["--check", "tests/unit/case.cjs"]
    assert entries[2]["source"] == "export default 1;\n"


def test_python_failure_still_runs_both_checks_and_fails(quality_repo):
    result = check(quality_repo, "python", QUALITY_TEST_FAIL="ruff")
    assert result.returncode == 1
    assert [entry["tool"] for entry in commands(quality_repo)] == ["ruff", "mypy"]


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
