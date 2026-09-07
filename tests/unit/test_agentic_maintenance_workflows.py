"""Protect the subscription workflow's event and credential boundaries."""

import re
import subprocess
import sys
import textwrap

import pytest

from testlib import repo_path


WORKFLOWS = repo_path('.github/workflows')


def source(name):
    return (WORKFLOWS / name).read_text()


def test_no_api_engine_or_generated_workflow_survives_migration():
    assert not list(WORKFLOWS.glob('*.lock.yml'))
    assert not list(WORKFLOWS.glob('*.md'))
    for path in WORKFLOWS.glob('agentic-*.yml'):
        text = path.read_text()
        for retired in ('llm.neurodesk.org', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'gh aw', 'GH_AW_'):
            assert retired not in text, path.name
    worker = repo_path('.github/scripts/agentic_worker.py').read_text()
    assert 'forced_login_method="chatgpt"' in worker
    assert '--dangerously-bypass-approvals-and-sandbox' not in worker


def test_issue_events_and_reporter_dispatch_share_one_worker():
    issue = source('agentic-issue.yml')
    reporter = repo_path('.github/scripts/report_workflow_failure.cjs').read_text()
    assert 'types: [opened, reopened, labeled]' in issue
    assert "github.event.issue.author_association" in issue
    assert "github.event.label.name == 'agentic-approved'" in issue
    assert "github.event_name == 'workflow_dispatch'" in issue
    assert 'workflow_dispatch:' in issue
    assert 'issue-number:' in issue
    assert 'kind: issue' in issue
    assert 'uses: ./.github/workflows/agentic-worker.yml' in issue
    assert 'agentic-operations' in issue and 'agentic-ignore' in issue
    assert 'workflow_id: "agentic-issue.yml"' in reporter
    assert not repo_path('.github/actions/report-job-failure/action.yml').exists()
    for path in WORKFLOWS.glob('*.yml'):
        assert 'create-an-issue@' not in path.read_text(), path.name
        assert 'report-job-failure' not in path.read_text(), path.name


def test_model_runner_and_publisher_have_separate_permissions_and_artifacts():
    workflow = source('agentic-worker.yml')
    prepare, candidate = workflow.split('  candidate:\n')
    assert "if: vars.AGENTIC_ENABLED == 'true'" in prepare
    candidate, publish = candidate.split('  publish:\n')
    assert 'runs-on: ubuntu-latest' in prepare and 'runs-on: ubuntu-latest' in publish
    assert "vars.AGENTIC_RUNNER || 'neurodesk-syd-arcrunner-neurodesktop'" in candidate
    assert "vars.AGENTIC_CODEX_HOME || '/var/lib/neurodesktop-codex'" in candidate
    assert 'group: neurodesktop-codex-subscription' in candidate
    assert 'queue: max' in candidate and 'cancel-in-progress: false' in candidate
    assert 'timeout-minutes: 125' in candidate
    assert 'persist-credentials: false' in candidate
    assert 'GH_TOKEN:' not in candidate and 'secrets.' not in candidate
    assert 'contents: write' not in candidate and 'pull-requests: write' not in candidate
    assert "needs.prepare.result == 'success' && needs.prepare.outputs.skip == 'false'" in candidate
    assert 'GH_TOKEN: ${{ github.token }}' in publish
    assert 'AGENTIC_CI_TOKEN: ${{ secrets.AGENTIC_CI_TOKEN }}' in publish
    assert 'auth.json' not in workflow
    assert candidate.index('check_agentic_sandbox.py') < candidate.index('Generate and independently')
    assert publish.index('name: agentic-candidate') < publish.index('name: agentic-task')
    assert 'agentic_worker.py" publish' in publish
    assert 'pytest' not in publish


def test_maintenance_runs_every_weekday_and_retains_manual_selection():
    workflow = source('agentic-maintenance.yml')
    assert "cron: '23 4 * * 1-5'" in workflow
    assert 'options: [updates, security, dead-code, test-coverage, refactoring]' in workflow
    assert 'kind: maintenance' in workflow
    assert 'matrix:' not in workflow
    assert 'uses: ./.github/workflows/agentic-worker.yml' in workflow


def test_readiness_is_manual_and_uses_trusted_default_branch_without_secrets():
    workflow = source('agentic-readiness.yml')
    assert 'workflow_dispatch:' in workflow
    assert 'schedule:' not in workflow
    assert 'github.event.repository.default_branch' in workflow
    assert 'check_agentic_sandbox.py' in workflow
    assert 'check_agentic_readiness.py' in workflow
    assert 'secrets.' not in workflow
    assert 'contents: write' not in workflow


@pytest.mark.parametrize('category', ['updates', 'security', 'dead-code', 'test-coverage', 'refactoring'])
def test_maintenance_manual_selector_executes(category, tmp_path):
    workflow = source('agentic-maintenance.yml')
    script = textwrap.dedent(workflow.split("python3 - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
    output = tmp_path / 'outputs'
    result = subprocess.run([sys.executable, '-c', script], env={
        'PATH': '/usr/bin:/bin', 'REQUESTED_CATEGORY': category, 'GITHUB_OUTPUT': str(output),
    }, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert output.read_text() == f'category={category}\n'


def test_maintenance_selector_refuses_unlisted_category(tmp_path):
    workflow = source('agentic-maintenance.yml')
    script = textwrap.dedent(workflow.split("python3 - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
    result = subprocess.run([sys.executable, '-c', script], env={
        'PATH': '/usr/bin:/bin', 'REQUESTED_CATEGORY': 'unknown', 'GITHUB_OUTPUT': str(tmp_path / 'outputs'),
    }, text=True, capture_output=True)
    assert result.returncode != 0
    assert not (tmp_path / 'outputs').exists()


def test_review_runs_only_for_coderabbit_on_owned_agent_prs():
    workflow = source('agentic-review.yml')
    assert "github.event.comment.user.login == 'coderabbitai[bot]'" in workflow
    assert "github.event.issue.user.login == 'github-actions[bot]'" in workflow
    assert "github.event.issue.state == 'open'" in workflow
    assert "contains(github.event.issue.labels.*.name, 'agentic-workflow')" in workflow
    assert "contains(github.event.comment.body, 'summarize by coderabbit.ai')" in workflow
    assert 'kind: review' in workflow
    assert 'drafts: true' in repo_path('.coderabbit.yaml').read_text()


def test_reporter_catalog_covers_each_top_level_test_deployment_and_agent_workflow():
    reporter = source('report-workflow-failure.yml')
    excluded = {'agentic-worker.yml', 'report-workflow-failure.yml', 'on-opening-issue.yml'}
    for path in WORKFLOWS.glob('*.yml'):
        if path.name in excluded:
            continue
        name = re.search(r'^name: (.+)$', path.read_text(), re.M).group(1).strip('"\'')
        assert f'- {name}\n' in reporter, path.name
