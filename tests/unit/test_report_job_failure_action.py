from testlib import repo_path


ACTION = repo_path(".github/actions/report-job-failure/action.yml")
WORKFLOW = repo_path(".github/workflows/issue-investigator.md")
LOCK = repo_path(".github/workflows/issue-investigator.lock.yml")
REVIEW_WORKFLOW = repo_path(".github/workflows/issue-investigator-review.md")
REVIEW_LOCK = repo_path(".github/workflows/issue-investigator-review.lock.yml")
CODERABBIT_CONFIG = repo_path(".coderabbit.yaml")
SHARED_MODELS_WORKFLOW = repo_path(".github/workflows/shared/agentic-models.md")
MODEL_ALIAS_JSON = '"neurodesk":["openai/glm-5.2","openai/kimi-k2.7"]'


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
    review_workflow = REVIEW_WORKFLOW.read_text()
    review_lock = REVIEW_LOCK.read_text()
    shared_models = SHARED_MODELS_WORKFLOW.read_text()
    model = "${{ vars.GH_AW_MODEL_AGENT_CODEX || vars.GH_AW_DEFAULT_MODEL_CODEX || 'neurodesk' }}"
    model_costs = '{"providers":{"openai":{"models":{"neurodesk":{"cost":{"input":"3e-06","output":"1.5e-05"}}}}}}'

    assert f"model: {model}" in workflow
    assert model in lock
    assert "neurodesk:\n    - openai/glm-5.2\n    - openai/kimi-k2.7" in shared_models
    assert "uses: .github/workflows/shared/agentic-models.md" in workflow
    assert "uses: .github/workflows/shared/agentic-models.md" in review_workflow
    assert MODEL_ALIAS_JSON in lock
    assert MODEL_ALIAS_JSON in review_lock
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
    workflow = WORKFLOW.read_text()

    assert "## Evidence Collection Budget" in workflow
    assert "Use a maximum of 8 read commands" in workflow
    assert "one representative failing job log" in workflow
    assert "read at most 2 representative failing job logs" in workflow
    assert "For matrix CI failures, do not inspect every matrix entry" in workflow
    assert "Use a maximum of 2 live network probes" in workflow
    assert "Do not retry a failing read or probe more than once" in workflow
    assert "call a safe-output tool immediately" in workflow


def test_coderabbit_auto_reviews_draft_agentic_pull_requests():
    config = CODERABBIT_CONFIG.read_text()

    assert "auto_review:" in config
    assert "drafts: true" in config


def test_issue_investigator_review_loop_is_scoped_to_coderabbit_agentic_prs():
    workflow = REVIEW_WORKFLOW.read_text()
    lock = REVIEW_LOCK.read_text()

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
    workflow = REVIEW_WORKFLOW.read_text()

    assert "complete current CodeRabbit review" in workflow
    assert "Do not treat the triggering comment as the complete review" in workflow
    assert "Verify every finding against the current PR head" in workflow
    assert "one coherent commit" in workflow
    assert "Run the smallest relevant tests" in workflow
    assert "@coderabbitai review" in workflow
    assert "no active actionable findings remain" in workflow
