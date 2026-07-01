from pathlib import Path


ACTION = Path(__file__).resolve().parents[1] / ".github/actions/report-job-failure/action.yml"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/issue-investigator.md"
LOCK = Path(__file__).resolve().parents[1] / ".github/workflows/issue-investigator.lock.yml"


def test_report_job_failure_gates_issue_investigator_dispatch():
    action = ACTION.read_text()

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
    action = ACTION.read_text()

    assert 'workflow_id: "issue-investigator.lock.yml"' in action
    assert 'const issueNumber = Number("${{ steps.dispatch_gate.outputs.issue_number }}");' in action
    assert "createWorkflowDispatch" in action


def test_issue_investigator_routes_codex_through_neurodesk_gateway():
    workflow = WORKFLOW.read_text()
    lock = LOCK.read_text()

    assert 'OPENAI_BASE_URL: "https://llm.neurodesk.org/openai"' in workflow
    assert "OPENAI_API_KEY: ${{ secrets.CODEX_API_KEY || secrets.OPENAI_API_KEY }}" in workflow
    assert "openai_base_url=" not in workflow
    assert "openai_base_url=" not in lock
    assert '\\"targets\\":{\\"openai\\":{\\"host\\":\\"llm.neurodesk.org\\"}}' in lock
    assert "--openai-api-base-path /openai" in lock
