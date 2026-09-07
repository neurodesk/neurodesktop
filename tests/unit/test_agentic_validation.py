"""Regression tests for the frozen agentic validation baseline."""

from pathlib import Path
import subprocess
import sys

from testlib import repo_path


VALIDATOR = repo_path("config/agentic/validate.py")


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
