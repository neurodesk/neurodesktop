"""Protect the subscription workflow's event and credential boundaries."""

import os
import re
import shlex
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
    assert 'ref: ${{ needs.prepare.outputs.base_sha }}' not in workflow
    assert 'ref: ${{ github.event.repository.default_branch }}' in candidate
    assert 'ref: ${{ github.event.repository.default_branch }}' in publish
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


def test_reporter_catalog_covers_each_top_level_test_deployment_and_agent_workflow():
    reporter = source('report-workflow-failure.yml')
    excluded = {'agentic-worker.yml', 'report-workflow-failure.yml', 'on-opening-issue.yml'}
    for path in WORKFLOWS.glob('*.yml'):
        if path.name in excluded:
            continue
        name = re.search(r'^name: (.+)$', path.read_text(), re.M).group(1).strip('"\'')
        assert f'- {name}\n' in reporter, path.name


def test_all_worker_checkouts_disable_persisted_credentials():
    workflow = source('agentic-worker.yml')
    assert workflow.count('persist-credentials: false') == workflow.count('uses: actions/checkout@')


def test_worker_stages_trusted_build_inputs_before_upload():
    workflow = source('agentic-worker.yml')
    prepare = workflow.split('  candidate:\n', 1)[0]
    assert 'agentic_worker.py stage' in prepare
    assert prepare.index('agentic_worker.py stage') < prepare.index('name: Save trusted task and controller')
    assert 'path: ${{ runner.temp }}/agentic-control/' in prepare


def test_readiness_and_unit_ci_exercise_the_offline_validator():
    import yaml

    for filename, job_name in (('agentic-readiness.yml', 'readiness'), ('unit-tests.yml', 'worker-validation')):
        job = yaml.safe_load(source(filename))['jobs'][job_name]
        commands = '\n'.join(step.get('run', '') for step in job['steps'])
        assert 'agentic_worker.py stage' in commands
        assert 'agentic_worker.py canary' in commands
        assert commands.index('agentic_worker.py stage') < commands.index('docker build')
        assert commands.index('docker build') < commands.index('agentic_worker.py canary')
        assert job['timeout-minutes'] >= 40
        checkout = next(step for step in job['steps'] if step.get('uses', '').startswith('actions/checkout@'))
        assert checkout['with']['ref'] == '${{ github.sha }}'
        assert checkout['with']['persist-credentials'] is False

    unit_job = yaml.safe_load(source('unit-tests.yml'))['jobs']['worker-validation']
    assert 'secrets.' not in str(unit_job)
    assert 'AGENTIC_CODEX_HOME' not in str(unit_job)


@pytest.mark.parametrize(('filename', 'job_name'), [
    ('agentic-readiness.yml', 'readiness'),
    ('unit-tests.yml', 'worker-validation'),
])
def test_canary_jobs_always_clean_up_before_uploading_evidence(filename, job_name):
    import yaml

    steps = yaml.safe_load(source(filename))['jobs'][job_name]['steps']
    cleanup_steps = [
        (index, step) for index, step in enumerate(steps)
        if 'cleanup' in shlex.split(step.get('run', ''))
    ]
    assert len(cleanup_steps) == 1, f'{filename} must have one canary cleanup step'
    cleanup_index, cleanup = cleanup_steps[0]
    assert cleanup['if'] == 'always()'
    assert shlex.split(cleanup['run']) == [
        'python3', '$RUNNER_TEMP/agentic-control/.github/scripts/agentic_worker.py',
        'cleanup', '--directory', '$RUNNER_TEMP/agentic-canary',
    ]
    canary_index = next(
        index for index, step in enumerate(steps)
        if 'canary' in shlex.split(step.get('run', ''))
    )
    upload_index = next(
        index for index, step in enumerate(steps)
        if step.get('uses', '').startswith('actions/upload-artifact@')
    )
    assert canary_index < cleanup_index < upload_index


def test_required_unit_context_gates_both_parallel_suites():
    import yaml

    jobs = yaml.safe_load(source('unit-tests.yml'))['jobs']
    assert jobs['unit-tests']['name'] == 'Checkout unit tests'
    for name in ('unit-tests', 'worker-validation'):
        assert not jobs[name].get('needs')
    gates = [job for job in jobs.values() if job.get('name') == 'pytest tests/unit']
    assert len(gates) == 1
    gate = gates[0]
    assert set(gate['needs']) == {'unit-tests', 'worker-validation'}
    assert gate['if'] == 'always()'
    step, = gate['steps']
    assert step['env'] == {
        'CHECKOUT_RESULT': '${{ needs.unit-tests.result }}',
        'WORKER_RESULT': '${{ needs.worker-validation.result }}',
    }
    assert 'continue-on-error' not in gate
    assert 'continue-on-error' not in step


@pytest.mark.parametrize('checkout', ['success', 'failure', 'cancelled', 'skipped', ''])
@pytest.mark.parametrize('worker', ['success', 'failure', 'cancelled', 'skipped', ''])
def test_required_unit_gate_only_accepts_two_successes(checkout, worker):
    import yaml

    jobs = yaml.safe_load(source('unit-tests.yml'))['jobs']
    gate = next(job for job in jobs.values() if job.get('name') == 'pytest tests/unit')
    step, = gate['steps']

    result = subprocess.run(
        ['bash', '-e', '-o', 'pipefail', '-c', step['run']],
        env={**os.environ, 'CHECKOUT_RESULT': checkout, 'WORKER_RESULT': worker},
        capture_output=True, text=True, timeout=5,
    )

    assert (result.returncode == 0) is (checkout == worker == 'success'), result.stdout + result.stderr


def test_auth_storage_readiness_requires_built_image_but_not_passing_canary():
    import yaml

    steps = yaml.safe_load(source('agentic-readiness.yml'))['jobs']['readiness']['steps']
    auth = next(step for step in steps if 'check_agentic_readiness.py' in step.get('run', ''))
    assert auth['if'] == "${{ !cancelled() && steps.build_worker.outcome == 'success' }}"
    build = next(step for step in steps if step.get('id') == 'build_worker')
    assert 'docker build --tag neurodesktop-agentic:' in build['run']
    assert steps.index(build) < steps.index(auth)
