"""Regression tests for the frozen agentic validation baseline."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

from testlib import load_source_module, repo_path


VALIDATOR = repo_path("config/agentic/validate.py")


@pytest.fixture
def validator():
    return load_source_module("agentic_validator_test", "/opt/validate.py", "config/agentic/validate.py")


def write_baseline(root):
    tests = root / "tests"
    unit = tests / "unit"
    unit.mkdir(parents=True)
    (tests / "testlib.py").write_text(
        "from pathlib import Path\n"
        "TESTS_DIR = Path(__file__).resolve().parent\n"
        "REPO_ROOT = TESTS_DIR.parent\n"
        "def repo_path(relative):\n"
        "    return REPO_ROOT / relative\n"
    )
    (tests / "conftest.py").write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\n")
    (tests / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    (unit / "test_subject.py").write_text(
        "from testlib import repo_path\n\n"
        "def test_base_behavior():\n"
        "    assert repo_path('subject.txt').read_text() == 'good\\n'\n"
    )


def write_candidate(root, subject):
    tests = root / "tests"
    unit = tests / "unit"
    unit.mkdir(parents=True)
    (root / "subject.txt").write_text(subject)
    # A candidate can make its own suite look healthy. It must not affect the
    # immutable baseline suite above.
    (tests / "conftest.py").write_text(
        "import pytest\n\n"
        "def pytest_collection_modifyitems(config, items):\n"
        "    for item in items:\n"
        "        item.add_marker(pytest.mark.skip(reason='candidate suppresses tests'))\n"
    )
    (tests / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n")
    (unit / "test_candidate.py").write_text("def test_candidate():\n    assert True\n")


def validate(baseline, workspace):
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--baseline", str(baseline), "--workspace", str(workspace)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def test_frozen_baseline_uses_patched_source_but_not_candidate_pytest_controls(tmp_path):
    baseline = tmp_path / "baseline"
    workspace = tmp_path / "workspace"
    write_baseline(baseline)
    write_candidate(workspace, "bad\n")

    result = validate(baseline, workspace)

    assert result.returncode == 1
    assert "=== frozen baseline against patched workspace ===" in result.stdout
    assert "test_base_behavior" in result.stdout
    assert "AssertionError" in result.stdout
    assert "=== candidate tests ===" in result.stdout


def test_frozen_baseline_redirects_testlib_to_patched_workspace(tmp_path):
    baseline = tmp_path / "baseline"
    workspace = tmp_path / "workspace"
    write_baseline(baseline)
    write_candidate(workspace, "good\n")

    result = validate(baseline, workspace)

    assert result.returncode == 0
    assert "1 passed" in result.stdout
    assert "=== candidate tests ===" in result.stdout


def test_frozen_baseline_reads_relative_workspace_sources_without_candidate_controls(tmp_path):
    baseline = tmp_path / "baseline"
    workspace = tmp_path / "workspace"
    write_baseline(baseline)
    write_candidate(workspace, "good\n")
    (baseline / "tests" / "unit" / "test_relative.py").write_text(
        "from pathlib import Path\n\n"
        "def test_relative_subject():\n"
        "    assert Path('subject.txt').read_text() == 'good\\n'\n"
    )

    passing = validate(baseline, workspace)

    assert passing.returncode == 0, passing.stdout
    assert "2 passed" in passing.stdout

    (workspace / "subject.txt").write_text("bad\n")
    failing = validate(baseline, workspace)

    assert failing.returncode == 1
    assert "test_relative_subject" in failing.stdout
    assert "2 failed" in failing.stdout


def test_candidate_failure_blocks_a_passing_frozen_baseline(tmp_path):
    baseline = tmp_path / "baseline"
    workspace = tmp_path / "workspace"
    write_baseline(baseline)
    (workspace / "subject.txt").parent.mkdir(parents=True)
    (workspace / "subject.txt").write_text("good\n")
    candidate = workspace / "tests" / "unit"
    candidate.mkdir(parents=True)
    (candidate / "test_candidate.py").write_text(
        "def test_candidate_failure():\n"
        "    assert False, 'candidate test failed'\n"
    )

    result = validate(baseline, workspace)

    assert result.returncode == 1
    assert "=== frozen baseline against patched workspace ===" in result.stdout
    assert "1 passed" in result.stdout
    assert "=== candidate tests ===" in result.stdout
    assert "candidate test failed" in result.stdout


def test_worker_pins_match_viewer_dependencies():
    import tomllib

    viewer = tomllib.loads(repo_path("extensions/astra-viewer/pyproject.toml").read_text())
    worker = repo_path("config/agentic/Dockerfile").read_text()
    for requirement in viewer["project"]["dependencies"]:
        if requirement.startswith(("astra-spec==", "astra-tools==", "anywidget==")):
            assert requirement in worker, f"Worker dependency drift: {requirement}"


@pytest.mark.parametrize("requirement", [
    "jupyterhub==6.0.1", "requests", "ipykernel", "websocket-client", "nibabel", "numpy",
    "openssh-client", "acl", "jq", "gcc", "libx11-dev", "xvfb", "x11-xserver-utils",
    "jsdom@26.1.0", "typescript@5.2.2", "esbuild@0.28.2",
    "@jupyterlab/docregistry@4.6.3", "@jupyterlab/coreutils@6.6.3",
    "@jupyterlab/services@7.6.3", "@lumino/widgets@2.9.0",
])
def test_worker_provisions_mandatory_ci_dependencies(requirement):
    worker = repo_path("config/agentic/Dockerfile").read_text()
    workflow = repo_path(".github/workflows/unit-tests.yml").read_text()

    assert requirement in workflow
    assert requirement in worker, f"Offline worker is missing CI dependency: {requirement}"


def test_worker_supports_system_python_shebangs():
    worker = repo_path("config/agentic/Dockerfile").read_text()

    assert "ln -s /usr/local/bin/python3 /usr/bin/python3" in worker


def test_worker_exposes_image_owned_dom_dependencies():
    worker = repo_path("config/agentic/Dockerfile").read_text()

    assert "npm install --prefix /opt/launcher-dom --no-audit --no-fund" in worker
    assert "ENV NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES=/opt/launcher-dom/node_modules" in worker


@pytest.mark.parametrize("module", ["test_jupyterlab_slurm_build.py", "test_audit_image_versions.py"])
def test_build_checks_run_from_a_tests_only_baseline(tmp_path, module):
    baseline = tmp_path / "baseline"
    workspace = tmp_path / "workspace"
    write_baseline(baseline)
    write_candidate(workspace, "good\n")
    shutil.copyfile(repo_path(f"tests/unit/{module}"), baseline / "tests" / "unit" / module)
    for relative in ("Dockerfile", "scripts/audit_image_versions.py"):
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_path(relative), destination)

    result = validate(baseline, workspace)

    assert result.returncode == 0, result.stdout


def test_validation_keeps_early_failure_and_loud_candidate_evidence(tmp_path):
    baseline, workspace = tmp_path / "baseline", tmp_path / "workspace"
    write_baseline(baseline)
    write_candidate(workspace, "bad\n")
    (workspace / "tests" / "conftest.py").write_text(
        "import os\nos.write(1, b'candidate noise' * 5000)\n"
    )
    (workspace / "tests" / "pytest.ini").write_text("[pytest]\naddopts = -s\n")

    result = validate(baseline, workspace)

    assert result.returncode == 1
    assert len(result.stdout.encode()) < 24000
    assert "test_base_behavior" in result.stdout
    assert "AssertionError" in result.stdout
    records = json.loads(result.stdout.split("VALIDATION_SUMMARY ")[-1])
    assert [(record["stage"], record["status"], record["returncode"]) for record in records] == [
        ("frozen_baseline", "failed", 1), ("candidate", "passed", 0),
    ]
    assert all(record["elapsed_seconds"] >= 0 for record in records)


@pytest.mark.parametrize("script", [
    "import os; os.write(1, b'x' * 65536)",
    "import os, time; os.write(2, b'x' * 65536); time.sleep(5)",
])
def test_validator_output_limit_fails_closed_with_bounded_tail(validator, monkeypatch, tmp_path, script):
    monkeypatch.setattr(validator, "MAX_STAGE_OUTPUT", 1024)

    result = validator.run("candidate", [sys.executable, "-c", script], cwd=tmp_path)

    assert result["status"] == "output_limit"
    assert result["returncode"] is not None
    assert result["output_truncated"] is True
    assert len(result["output_tail"].encode()) <= validator.STAGE_TAIL_BYTES


def test_validator_timeout_kills_descendants_and_keeps_partial_output(validator, tmp_path):
    marker = tmp_path / "descendant-finished"
    child = f"import time; from pathlib import Path; time.sleep(0.4); Path({str(marker)!r}).touch()"
    script = (
        "import subprocess, sys, time; print('before timeout', flush=True); "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(5)"
    )

    result = validator.run("frozen_baseline", [sys.executable, "-c", script], cwd=tmp_path, timeout=0.1)

    assert result["status"] == "timed_out"
    assert "before timeout" in result["output_tail"]
    time.sleep(0.5)
    assert not marker.exists()


@pytest.mark.parametrize("redirect_output", [True, False], ids=["closed-pipe", "inherited-pipe"])
@pytest.mark.parametrize("returncode,status", [(0, "passed"), (7, "failed")])
def test_validator_cleans_up_children_after_leader_exit(validator, tmp_path, redirect_output, returncode, status):
    redirection = ">/dev/null 2>&1" if redirect_output else ""
    script = (
        f"sleep 30 {redirection} & echo $! > child.pid; "
        f"printf 'leader output\\n'; exit {returncode}"
    )

    try:
        result = validator.run("candidate", ["sh", "-c", script], cwd=tmp_path, timeout=2)

        assert result["status"] == status
        assert result["returncode"] == returncode
        assert result["elapsed_seconds"] < 1.5
        assert result["output_tail"] == "leader output\n"
        child = int((tmp_path / "child.pid").read_text())
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                child_state = Path(f"/proc/{child}/stat").read_text().split()[2]
            except FileNotFoundError:
                break
            if child_state == "Z":
                break
            time.sleep(0.01)
        else:
            pytest.fail("Stage descendant survived its leader")
    finally:
        if (tmp_path / "child.pid").exists():
            try:
                os.kill(int((tmp_path / "child.pid").read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_validator_retains_buffered_output_after_leader_exit(validator, tmp_path):
    payload = b"x" * 200000 + b"final buffered output\n"
    script = "import os; os.write(1, b'x' * 200000 + b'final buffered output\\n')"

    result = validator.run("candidate", [sys.executable, "-c", script], cwd=tmp_path)

    assert result["status"] == "passed"
    assert result["returncode"] == 0
    assert result["output_tail"] == payload[-validator.STAGE_TAIL_BYTES:].decode()
    assert result["output_truncated"] is True


@pytest.mark.parametrize("failure,status", [
    ("import time; time.sleep(5)", "timed_out"),
    ("import os; os.write(1, b'x' * 65536)", "output_limit"),
])
def test_failed_bounded_baseline_still_runs_candidate(tmp_path, failure, status):
    baseline, workspace = tmp_path / "baseline", tmp_path / "workspace"
    write_baseline(baseline)
    write_candidate(workspace, "good\n")
    (baseline / "tests" / "unit" / "test_subject.py").write_text(
        "def test_failure(capfd):\n    with capfd.disabled():\n        " + failure + "\n"
    )
    (workspace / "tests" / "conftest.py").write_text("")
    (workspace / "tests" / "pytest.ini").unlink()
    bootstrap = (
        "import functools, runpy, sys; "
        "module = runpy.run_path(sys.argv[1]); main = module['main']; "
        "main.__globals__['MAX_STAGE_OUTPUT'] = 4096; "
        "main.__globals__['run'] = functools.partial(module['run'], timeout=2); "
        "raise SystemExit(main(sys.argv[2:]))"
    )

    result = subprocess.run([
        sys.executable, "-c", bootstrap, str(VALIDATOR),
        "--baseline", str(baseline), "--workspace", str(workspace),
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)

    assert result.returncode == 1, result.stdout
    records = json.loads(result.stdout.split("VALIDATION_SUMMARY ")[-1])
    assert [(record["stage"], record["status"]) for record in records] == [
        ("frozen_baseline", status), ("candidate", "passed"),
    ]


@pytest.fixture
def lint_checkout(validator, tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    lint = workspace / "tools" / "javascript-lint"
    lint.mkdir(parents=True)
    baked = tmp_path / "baked" / "node_modules"
    baked.mkdir(parents=True)
    (baked / "eslint-marker").write_text("trusted locked install")
    (workspace / ".gitignore").write_text("tools/javascript-lint/node_modules\n")
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    monkeypatch.setattr(validator, "LINT_NODE_MODULES", baked)
    return workspace, lint / "node_modules", baked


def test_prepare_workspace_cli_links_baked_dependencies(validator, lint_checkout):
    workspace, target, baked = lint_checkout

    assert validator.main(["--prepare-workspace", str(workspace)]) == 0

    assert target.is_symlink()
    assert target.resolve() == baked
    assert (target / "eslint-marker").read_text() == "trusted locked install"


def test_lint_setup_restores_ignored_baked_install_after_git_clean(validator, lint_checkout):
    workspace, target, baked = lint_checkout

    validator.prepare_checkout_dependencies(workspace)
    subprocess.run(["git", "-C", str(workspace), "add", ".gitignore"], check=True)
    assert subprocess.check_output(["git", "-C", str(workspace), "ls-files", "--", "tools"]) == b""
    subprocess.run(["git", "-C", str(workspace), "clean", "-fdx"], check=True, capture_output=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    validator.prepare_checkout_dependencies(workspace)
    validator.prepare_checkout_dependencies(workspace)

    assert target.is_symlink()
    assert target.resolve() == baked
    assert (target / "eslint-marker").read_text() == "trusted locked install"


@pytest.mark.parametrize("redirect", ["tools", "tools/javascript-lint", "tools/javascript-lint/node_modules"])
def test_lint_setup_rejects_candidate_path_redirection(validator, lint_checkout, tmp_path, redirect):
    workspace, _, _ = lint_checkout
    external = tmp_path / "outside"
    external.mkdir()
    path = workspace / redirect
    if path.exists():
        shutil.rmtree(path)
    path.symlink_to(external)

    with pytest.raises(ValueError, match="dependency path"):
        validator.prepare_checkout_dependencies(workspace)

    assert list(external.iterdir()) == []


def test_lint_setup_rejects_existing_candidate_dependency_directory(validator, lint_checkout):
    workspace, target, _ = lint_checkout
    target.mkdir()

    with pytest.raises(ValueError, match="dependency path"):
        validator.prepare_checkout_dependencies(workspace)


def test_lint_setup_rejects_dependency_path_without_ignore_rule(validator, lint_checkout):
    workspace, target, _ = lint_checkout
    (workspace / ".gitignore").write_text("")

    with pytest.raises(ValueError, match="ignored"):
        validator.prepare_checkout_dependencies(workspace)

    assert not target.exists()


def test_lint_setup_rejects_tracked_link_even_when_it_targets_baked_install(validator, lint_checkout):
    workspace, target, baked = lint_checkout
    target.symlink_to(baked)
    subprocess.run(["git", "-C", str(workspace), "add", "--force", str(target)], check=True)

    with pytest.raises(ValueError, match="untracked"):
        validator.prepare_checkout_dependencies(workspace)


def test_worker_installs_trusted_locked_eslint():
    worker = repo_path("config/agentic/Dockerfile").read_text()
    assert "COPY javascript-lint/package.json javascript-lint/package-lock.json /opt/javascript-lint/" in worker
    assert "npm ci --prefix /opt/javascript-lint --ignore-scripts --no-audit --no-fund" in worker
    assert "libc6-dev" in worker
