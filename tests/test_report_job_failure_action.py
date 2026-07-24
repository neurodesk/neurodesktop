from pathlib import Path

import pytest


ACTION = Path(__file__).resolve().parents[1] / ".github/actions/report-job-failure/action.yml"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/issue-investigator.md"
LOCK = Path(__file__).resolve().parents[1] / ".github/workflows/issue-investigator.lock.yml"
REVIEW_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/issue-investigator-review.md"
)
REVIEW_LOCK = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/issue-investigator-review.lock.yml"
)
CODERABBIT_CONFIG = Path(__file__).resolve().parents[1] / ".coderabbit.yaml"


def _read_repo_file(path: Path) -> str:
    if path.exists():
        return path.read_text()
    if Path(__file__).resolve().parents[1] == Path("/opt"):
        pytest.skip("repo-only .github workflow files are not bundled into /opt/tests")
    return path.read_text()


def test_report_job_failure_gates_issue_investigator_dispatch():
    action = _read_repo_file(ACTION)

    assert "id: failure_comment" in action
    assert "id: dispatch_gate" in action
    assert "neurodesktop-job-failure-report" in action
    assert "neurodesktop-issue-investigator-dispatched" in action
    assert "canonical run issue" in action
    assert "first failure comment for run" in action
    assert "first dispatch marker for run" in action
    assert "const dispatchMarker = await github.rest.issues.createComment" in action
    assert "steps.dispatch_gate.outputs.should_dispatch == 'true'" in action


def test_report_job_failure_dispatch_uses_canonical_issue_number():
    action = _read_repo_file(ACTION)

    assert 'workflow_id: "issue-investigator.lock.yml"' in action
    assert 'const issueNumber = Number("${{ steps.dispatch_gate.outputs.issue_number }}");' in action
    assert "createWorkflowDispatch" in action


def test_issue_investigator_routes_codex_through_neurodesk_gateway():
    workflow = _read_repo_file(WORKFLOW)
    lock = _read_repo_file(LOCK)
    model = "${{ vars.GH_AW_MODEL_AGENT_CODEX || vars.GH_AW_DEFAULT_MODEL_CODEX || 'neurodesk' }}"
    model_costs = '{"providers":{"openai":{"models":{"neurodesk":{"cost":{"input":"3e-06","output":"1.5e-05"}}}}}}'

    assert f"model: {model}" in workflow
    assert model in lock
    assert "kimi-k2.7" not in workflow
    assert "kimi-k2.7" not in lock
    assert 'OPENAI_BASE_URL: "https://llm.neurodesk.org/openai"' in workflow
    assert "OPENAI_API_KEY: ${{ secrets.CODEX_API_KEY || secrets.OPENAI_API_KEY }}" in workflow
    assert "models:\n  providers:\n    openai:\n      models:\n        neurodesk:" in workflow
    assert 'input: "3e-06"' in workflow
    assert 'output: "1.5e-05"' in workflow
    assert f"GH_AW_INFO_MODEL_COSTS: '{model_costs}'" in lock
    assert "max-ai-credits: -1\n" in workflow
    assert 'GH_AW_MAX_AI_CREDITS: "-1"' in lock
    assert '"maxAiCredits":' not in lock
    assert '\\"maxAiCredits\\":' not in lock
    assert "max-turn-cache-misses: 2000\n" in workflow
    assert "openai_base_url=" not in workflow
    assert "openai_base_url=" not in lock
    assert '"targets":{"openai":{"host":"llm.neurodesk.org"}}' in lock
    assert '"maxCacheMisses":2000,"targets"' in lock
    assert "--openai-api-base-path /openai" in lock


def test_issue_investigator_has_bounded_evidence_collection_guardrails():
    workflow = _read_repo_file(WORKFLOW)

    assert "## Evidence Collection Budget" in workflow
    assert "Use a maximum of 8 read commands" in workflow
    assert "one representative failing job log" in workflow
    assert "read at most 2 representative failing job logs" in workflow
    assert "For matrix CI failures, do not inspect every matrix entry" in workflow
    assert "Use a maximum of 2 live network probes" in workflow
    assert "Do not retry a failing read or probe more than once" in workflow
    assert "call a safe-output tool immediately" in workflow


def test_coderabbit_auto_reviews_draft_agentic_pull_requests():
    config = _read_repo_file(CODERABBIT_CONFIG)

    assert "auto_review:" in config
    assert "drafts: true" in config


def test_issue_investigator_review_loop_is_scoped_to_coderabbit_agentic_prs():
    workflow = _read_repo_file(REVIEW_WORKFLOW)
    lock = _read_repo_file(REVIEW_LOCK)

    assert "issue_comment:" in workflow
    assert "types: [created, edited]" in workflow
    assert 'bots: ["coderabbitai[bot]"]' in workflow
    assert "github.event.issue.state == 'open'" in workflow
    assert "github.event.issue.user.login == 'github-actions[bot]'" in workflow
    assert "agentic-workflow" in workflow
    assert "[issue-investigator] " in workflow
    assert "summarize by coderabbit.ai" in workflow
    assert "push-to-pull-request-branch:" in workflow
    assert "target: triggering" in workflow
    assert "required-labels: [agentic-workflow]" in workflow
    assert 'required-title-prefix: "[issue-investigator] "' in workflow

    assert "issue_comment:" in lock
    assert "coderabbitai[bot]" in lock
    assert "push_to_pull_request_branch" in lock


def test_issue_investigator_review_loop_batches_and_rechecks_findings():
    workflow = _read_repo_file(REVIEW_WORKFLOW)

    assert "complete current CodeRabbit review" in workflow
    assert "Do not treat the triggering comment as the complete review" in workflow
    assert "Verify every finding against the current PR head" in workflow
    assert "one coherent commit" in workflow
    assert "Run the smallest relevant tests" in workflow
    assert "@coderabbitai review" in workflow
    assert "no active actionable findings remain" in workflow
