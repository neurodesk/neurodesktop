from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github/workflows"
SHARED_WORKFLOW = WORKFLOW_DIR / "shared/maintenance-base.md"
REVIEW_WORKFLOW = WORKFLOW_DIR / "maintenance-review.md"
REVIEW_LOCK = WORKFLOW_DIR / "maintenance-review.lock.yml"
CODERABBIT_CONFIG = REPO_ROOT / ".coderabbit.yaml"

MAINTENANCE_WORKFLOWS = {
    "maintenance-test-pruning": "test-pruning",
    "maintenance-test-coverage": "test-coverage",
    "maintenance-updates": "updates",
    "maintenance-abstraction-police": "abstraction-police",
    "maintenance-dead-code": "dead-code",
    "maintenance-docs-drift": "docs-drift",
    "maintenance-flaky-tests": "flaky-tests",
}


def _read_repo_file(path: Path) -> str:
    if path.exists():
        return path.read_text()
    if REPO_ROOT == Path("/opt"):
        pytest.skip("repo-only agentic workflow files are not bundled into /opt/tests")
    return path.read_text()


def test_daily_maintenance_workflows_share_the_bounded_pr_contract():
    shared_workflow = _read_repo_file(SHARED_WORKFLOW)

    assert "Search open pull requests" in shared_workflow
    assert "Make at most one coherent maintenance change per run" in shared_workflow
    assert "call `noop`" in shared_workflow
    assert 'title-prefix: "[maintenance] "' in shared_workflow
    assert "labels: [agentic-workflow]" in shared_workflow
    assert "draft: true" in shared_workflow
    assert "max-patch-files: 20" in shared_workflow
    assert (
        'branch-prefix: "agentic/maintenance-${{ github.aw.import-inputs.category }}-"'
        in shared_workflow
    )
    assert '".github/workflows/**"' not in shared_workflow


def test_daily_maintenance_sources_are_scheduled_scoped_and_compiled():
    compiled_crons = set()

    for workflow_id, category in MAINTENANCE_WORKFLOWS.items():
        source = _read_repo_file(WORKFLOW_DIR / f"{workflow_id}.md")
        lock = _read_repo_file(WORKFLOW_DIR / f"{workflow_id}.lock.yml")

        assert "schedule: daily" in source
        assert "workflow_dispatch:" in source
        assert "actions: read" in source
        assert "id: codex" in source
        assert "max-daily-ai-credits: -1" in source
        assert 'OPENAI_BASE_URL: "https://llm.neurodesk.org/openai"' in source
        assert "uses: .github/workflows/shared/maintenance-base.md" in source
        assert f"category: {category}" in source

        assert "schedule:" in lock
        assert "workflow_dispatch:" in lock
        assert "create_pull_request" in lock
        assert "[maintenance] " in lock
        assert f"agentic/maintenance-{category}-" in lock
        assert "agentic-workflow" in lock

        cron_lines = [
            line.strip()
            for line in lock.splitlines()
            if line.strip().startswith("- cron:")
        ]
        assert len(cron_lines) == 1
        assert cron_lines[0].endswith('* * *"')
        compiled_crons.add(cron_lines[0])

    assert len(compiled_crons) == len(MAINTENANCE_WORKFLOWS)


def test_maintenance_review_loop_consumes_coderabbit_and_requests_rereview():
    workflow = _read_repo_file(REVIEW_WORKFLOW)
    lock = _read_repo_file(REVIEW_LOCK)
    coderabbit = _read_repo_file(CODERABBIT_CONFIG)

    assert "drafts: true" in coderabbit
    assert 'bots: ["coderabbitai[bot]"]' in workflow
    assert "github.event.issue.user.login == 'github-actions[bot]'" in workflow
    assert "startsWith(github.event.issue.title, '[maintenance] ')" in workflow
    assert "summarize by coderabbit.ai" in workflow
    assert "complete current CodeRabbit review" in workflow
    assert "Verify every finding against the current PR head" in workflow
    assert "one coherent commit" in workflow
    assert "@coderabbitai review" in workflow
    assert "no active actionable findings remain" in workflow
    assert 'required-title-prefix: "[maintenance] "' in workflow
    assert "policy: fallback-to-issue" in workflow
    assert '- "package-lock.json"' in workflow

    assert "issue_comment:" in lock
    assert "coderabbitai[bot]" in lock
    assert "push_to_pull_request_branch" in lock
    assert "[maintenance] " in lock
