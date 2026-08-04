from testlib import repo_path


WORKFLOW_DIR = repo_path(".github/workflows")
SHARED_WORKFLOW = WORKFLOW_DIR / "shared/maintenance-base.md"
SHARED_MODELS_WORKFLOW = WORKFLOW_DIR / "shared/agentic-models.md"
REVIEW_WORKFLOW = WORKFLOW_DIR / "maintenance-review.md"
REVIEW_LOCK = WORKFLOW_DIR / "maintenance-review.lock.yml"
CODERABBIT_CONFIG = repo_path(".coderabbit.yaml")
ACTIONLINT_CONFIG = repo_path(".github/actionlint.yaml")
MODEL_ALIAS_JSON = '"neurodesk":["openai/glm-5.2","openai/kimi-k2.7","openai/minimax-m2"]'
RADAR_WORKFLOW = "package-update-radar"

MAINTENANCE_WORKFLOWS = {
    "maintenance-test-pruning": "test-pruning",
    "maintenance-test-coverage": "test-coverage",
    "maintenance-updates": "updates",
    "maintenance-abstraction-police": "abstraction-police",
    "maintenance-dead-code": "dead-code",
    "maintenance-docs-drift": "docs-drift",
    "maintenance-flaky-tests": "flaky-tests",
}


def test_weekly_maintenance_workflows_share_the_bounded_pr_contract():
    shared_workflow = SHARED_WORKFLOW.read_text()
    normalized_workflow = " ".join(shared_workflow.split())

    assert "Search open pull requests" in shared_workflow
    assert "Make at most one coherent maintenance change per run" in shared_workflow
    assert "call `noop`" in shared_workflow
    assert 'title-prefix: "[maintenance] "' in shared_workflow
    assert "Never use the shell `gh` CLI" in shared_workflow
    assert "Work directly without sub-agents" in shared_workflow
    assert "The run is complete only after exactly one safe-output tool call" in shared_workflow
    assert "Never finish with a plan, progress message" in shared_workflow
    assert "stop investigating and make the required safe-output call" in normalized_workflow
    assert "labels: [agentic-workflow]" in shared_workflow
    assert "draft: true" in shared_workflow
    assert "max-patch-files: 20" in shared_workflow
    assert (
        'branch-prefix: "agentic/maintenance-${{ github.aw.import-inputs.category }}-"'
        in shared_workflow
    )
    assert '".github/workflows/**"' not in shared_workflow


def test_weekly_maintenance_sources_are_scheduled_scoped_and_compiled():
    compiled_crons = set()

    for workflow_id, category in MAINTENANCE_WORKFLOWS.items():
        source = (WORKFLOW_DIR / f"{workflow_id}.md").read_text()
        lock = (WORKFLOW_DIR / f"{workflow_id}.lock.yml").read_text()
        normalized_lock = " ".join(lock.split())

        assert "schedule: weekly" in source
        assert "schedule: daily" not in source
        assert "workflow_dispatch:" in source
        assert "actions: read" in source
        assert "id: codex" in source
        assert 'args: ["-c", "features.multi_agent=false"]' in source
        assert "max-daily-ai-credits: -1" in source
        assert 'OPENAI_BASE_URL: "https://llm.neurodesk.org/openai"' in source
        assert "uses: .github/workflows/shared/agentic-models.md" in source
        assert "uses: .github/workflows/shared/maintenance-base.md" in source
        assert f"category: {category}" in source

        assert "schedule:" in lock
        assert "workflow_dispatch:" in lock
        assert "create_pull_request" in lock
        assert "[maintenance] " in lock
        assert f"agentic/maintenance-{category}-" in lock
        assert "agentic-workflow" in lock
        assert MODEL_ALIAS_JSON in lock
        assert "features.multi_agent=false" in lock
        assert (
            "The run is complete only after exactly one safe-output tool call"
            in normalized_lock
        )

        cron_lines = [
            line.strip()
            for line in lock.splitlines()
            if line.strip().startswith("- cron:")
        ]
        assert len(cron_lines) == 1
        cron_fields = cron_lines[0].split('"', maxsplit=2)[1].split()
        assert len(cron_fields) == 5
        assert cron_fields[2:4] == ["*", "*"]
        assert cron_fields[4] != "*"
        compiled_crons.add(cron_lines[0])

    assert len(compiled_crons) == len(MAINTENANCE_WORKFLOWS)


def test_package_update_radar_reports_without_write_access():
    source = (WORKFLOW_DIR / f"{RADAR_WORKFLOW}.md").read_text()
    lock = (WORKFLOW_DIR / f"{RADAR_WORKFLOW}.lock.yml").read_text()

    assert "schedule: weekly" in source
    assert "schedule: daily" not in source
    assert "workflow_dispatch:" in source
    assert "actions: read" in source
    assert "id: codex" in source
    assert 'args: ["-c", "features.multi_agent=false"]' in source
    assert "max-daily-ai-credits: -1" in source
    assert 'OPENAI_BASE_URL: "https://llm.neurodesk.org/openai"' in source
    assert "uses: .github/workflows/shared/agentic-models.md" in source
    assert MODEL_ALIAS_JSON in lock

    # The radar only surveys. `maintenance-updates` is the workflow allowed to
    # apply an upgrade, so the radar must carry no code-write path at all.
    assert "uses: .github/workflows/shared/maintenance-base.md" not in source
    assert "create-pull-request" not in source
    assert "create_pull_request" not in lock
    assert "push_to_pull_request_branch" not in lock

    assert 'title-prefix: "[package-updates] "' in source
    assert "labels: [agentic-workflow, dependencies]" in source
    assert "hide-older-comments: true" in source
    assert "create_issue" in lock
    assert "add_comment" in lock
    assert "[package-updates] " in lock
    assert "agentic-workflow" in lock
    assert "features.multi_agent=false" in lock
    assert "{{#runtime-import .github/workflows/package-update-radar.md}}" in lock

    # Keep the survey short enough to reserve a final model turn for its
    # mandatory safe output. Run 30836841541 exhausted itself on large,
    # sequential reads and ended with an ordinary progress message.
    assert "Never use the shell `gh` CLI" in source
    assert "Use at most 4\n  repository-read shell commands" in source
    assert "Use at most 12 upstream version probes" in source
    assert "never print a whole file or an unfiltered registry" in source
    assert "The run is complete only after exactly one safe-output tool call" in source
    assert "partial coverage is preferable" in source


def test_package_update_radar_keeps_a_distinct_weekly_slot():
    crons = {}

    for workflow_id in (*MAINTENANCE_WORKFLOWS, RADAR_WORKFLOW):
        lock = (WORKFLOW_DIR / f"{workflow_id}.lock.yml").read_text()
        cron_lines = [
            line.strip()
            for line in lock.splitlines()
            if line.strip().startswith("- cron:")
        ]
        assert len(cron_lines) == 1
        crons[workflow_id] = cron_lines[0]

    assert len(set(crons.values())) == len(crons)


def test_package_update_radar_lock_has_scoped_actionlint_exceptions():
    config = ACTIONLINT_CONFIG.read_text()

    assert f".github/workflows/{RADAR_WORKFLOW}.lock.yml:" in config


def test_agentic_model_alias_prefers_glm_then_falls_back_to_kimi():
    models_workflow = SHARED_MODELS_WORKFLOW.read_text()

    assert "neurodesk:\n    - openai/glm-5.2\n    - openai/kimi-k2.7" in models_workflow


def test_maintenance_review_loop_consumes_coderabbit_and_requests_rereview():
    workflow = REVIEW_WORKFLOW.read_text()
    lock = REVIEW_LOCK.read_text()
    coderabbit = CODERABBIT_CONFIG.read_text()

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
    assert "uses: .github/workflows/shared/agentic-models.md" in workflow

    assert "issue_comment:" in lock
    assert "coderabbitai[bot]" in lock
    assert "push_to_pull_request_branch" in lock
    assert "[maintenance] " in lock
    assert MODEL_ALIAS_JSON in lock
