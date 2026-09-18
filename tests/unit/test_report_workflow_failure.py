import json
import subprocess

import pytest

from testlib import repo_path


SCRIPT = repo_path(".github/scripts/report_workflow_failure.cjs")
WORKFLOW = repo_path(".github/workflows/report-workflow-failure.yml")

HARNESS = r"""
const fs = require('node:fs');
const { reportWorkflowFailure, WORKFLOWS } = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const issues = input.issues || [];
const comments = input.comments || [];
const calls = [];
let dispatchFailures = input.dispatchFailures || 0;
let markerFailures = input.markerFailures || 0;
const bot = { login: 'github-actions[bot]' };
const rest = {
  actions: {
    listJobsForWorkflowRunAttempt() {},
    async createWorkflowDispatch(params) {
      calls.push({ type: 'dispatch', ...params });
      if (dispatchFailures-- > 0) throw new Error('dispatch unavailable');
    },
  },
  issues: {
    listForRepo() {},
    listComments() {},
    async create(params) {
      calls.push({ type: 'issue', ...params });
      const issue = { number: issues.length + 100, state: 'open', user: bot, ...params };
      issues.push(issue);
      return { data: issue };
    },
    async update(params) {
      calls.push({ type: 'update', ...params });
      Object.assign(issues.find(issue => issue.number === params.issue_number), params);
    },
    async createComment(params) {
      if (params.body.startsWith('<!-- neurodesktop-workflow-failure-dispatched:') && markerFailures-- > 0) {
        throw new Error('marker unavailable');
      }
      calls.push({ type: 'comment', ...params });
      const comment = { id: comments.length + 1, user: bot, ...params };
      comments.push(comment);
      return { data: comment };
    },
  },
};
const github = {
  rest,
  async paginate(method, params) {
    calls.push({ type: 'read', ...params });
    if (method === rest.actions.listJobsForWorkflowRunAttempt) return input.jobs || [];
    if (method === rest.issues.listForRepo) return issues;
    if (method === rest.issues.listComments) return comments.filter(c => c.issue_number === params.issue_number);
    throw new Error('Unexpected API method');
  },
};
(async () => {
  const results = [];
  for (const override of input.runs || [{}]) {
    const context = {
      repo: { owner: 'NeuroDesk', repo: 'neurodesktop' },
      serverUrl: 'https://github.com',
      payload: {
        repository: { default_branch: 'main' },
        workflow_run: {
          id: 1234, run_attempt: 1, status: 'completed', conclusion: 'failure',
          name: 'Unit tests', repository: { full_name: 'NeuroDesk/neurodesktop' },
          event: 'pull_request', head_branch: 'main', head_sha: 'a'.repeat(40),
          head_repository: { full_name: 'NeuroDesk/neurodesktop' },
          pull_requests: [{ number: 23 }], ...override,
        },
      },
    };
    try {
      results.push(await reportWorkflowFailure({ github, context, core: { info() {} }, repairEnabled: override.repairEnabled ?? input.repairEnabled ?? true }));
    } catch (error) {
      results.push({ error: error.message });
    }
  }
  process.stdout.write(JSON.stringify({ issues, comments, calls, results, workflows: [...WORKFLOWS] }));
})();
"""


def run_reporter(**scenario):
    result = subprocess.run(
        ["node", "-e", HARNESS, str(SCRIPT)],
        input=json.dumps(scenario), text=True, capture_output=True, check=True,
    )
    output = json.loads(result.stdout)
    for call in output["calls"]:
        assert call["owner"] == "NeuroDesk"
        assert call["repo"] == "neurodesktop"
    return output


def calls_of(output, kind):
    return [call for call in output["calls"] if call["type"] == kind]


def marker_issue(number=42, user="github-actions[bot]", state="open"):
    return {
        "number": number, "state": state, "user": {"login": user},
        "body": "<!-- neurodesktop-workflow-failure: run=1234 -->\nFailure report",
    }


def marker_comment(kind, user="github-actions[bot]", issue=42):
    return {
        "issue_number": issue, "user": {"login": user},
        "body": f"<!-- neurodesktop-workflow-failure-{kind}: run=1234 attempt=1 -->\nDetails",
    }


def test_completed_run_aggregates_matrix_failures_and_dispatches_default_branch():
    output = run_reporter(jobs=[
        {"id": 1, "name": "amd64", "conclusion": "failure", "steps": [
            {"name": "pytest", "conclusion": "failure"},
            {"name": "checkout", "conclusion": "success"},
        ]},
        {"id": 2, "name": "arm64", "conclusion": "timed_out"},
        {"id": 3, "name": "lint", "conclusion": "success"},
    ])
    assert len(output["issues"]) == 1
    assert "#23" in output["issues"][0]["body"]
    comment = output["comments"][0]["body"]
    assert "2 failed or timed-out jobs" in comment
    assert "amd64" in comment and "arm64" in comment and "pytest" in comment
    assert "lint" not in comment and "checkout" not in comment
    assert "/actions/runs/1234/job/2" in comment
    dispatch = calls_of(output, "dispatch")[0]
    assert dispatch["workflow_id"] == "agentic-issue.yml"
    assert dispatch["ref"] == "main"
    assert dispatch["inputs"] == {"issue-number": "100"}
    assert any(call.get("attempt_number") == 1 for call in output["calls"])


@pytest.mark.parametrize("head_repository", ["NeuroDesk/neurodesktop", "contributor/neurodesktop"])
def test_feature_branch_failure_is_reported_without_default_branch_dispatch(head_repository):
    output = run_reporter(runs=[{
        "head_branch": "user-branch",
        "head_repository": {"full_name": head_repository},
    }])

    assert len(output["issues"]) == 1
    issue_body = output["issues"][0]["body"]
    assert "feature-branch failure" in issue_body
    assert "issue repair edits the default branch" in issue_body
    assert "manually dispatch repair" not in issue_body
    assert "Related pull requests: #23." in issue_body
    assert calls_of(output, "comment")
    assert not calls_of(output, "dispatch")
    assert not any("failure-dispatched:" in c["body"] for c in output["comments"])


def test_malicious_job_name_cannot_change_its_report_link():
    output = run_reporter(jobs=[{
        "id": 99,
        "name": "job](https://attacker.invalid)\\[",
        "conclusion": "failure",
    }])
    comment = output["comments"][0]["body"]
    trusted_url = "https://github.com/NeuroDesk/neurodesktop/actions/runs/1234/job/99"
    assert f"]({trusted_url})" in comment
    assert "](https://attacker.invalid)" not in comment
    assert "job\\]\\(https://attacker.invalid\\)\\\\\\[" in comment


def test_duplicate_delivery_reuses_issue_comment_and_successful_dispatch():
    output = run_reporter(runs=[{}, {}])
    assert len(output["issues"]) == 1
    assert len(output["comments"]) == 2
    assert len(calls_of(output, "dispatch")) == 1
    assert output["results"][1]["dispatched"] is False


def test_failed_dispatch_remains_retryable_without_duplicate_evidence():
    output = run_reporter(runs=[{}, {}], dispatchFailures=1)
    assert output["results"][0] == {"error": "dispatch unavailable"}
    assert output["results"][1]["dispatched"] is True
    assert len(calls_of(output, "dispatch")) == 2
    assert len(output["issues"]) == 1
    assert len(output["comments"]) == 2
    assert [call["type"] for call in output["calls"] if call["type"] != "read"] == [
        "issue", "comment", "dispatch", "dispatch", "comment",
    ]


def test_crash_after_dispatch_allows_at_least_once_redelivery():
    output = run_reporter(runs=[{}, {}], markerFailures=1)
    assert output["results"][0] == {"error": "marker unavailable"}
    assert output["results"][1]["dispatched"] is True
    assert len(calls_of(output, "dispatch")) == 2
    assert len(output["issues"]) == 1


def test_rerun_attempt_gets_new_evidence_and_dispatch_on_existing_issue():
    output = run_reporter(runs=[{}, {"run_attempt": 2}])
    assert len(output["issues"]) == 1
    assert len(output["comments"]) == 4
    assert len(calls_of(output, "dispatch")) == 2
    assert "attempt=2" in output["comments"][2]["body"]


@pytest.mark.parametrize("name", ["Agentic issue repair", "Agentic maintenance", "Agentic PR review"])
def test_agent_failures_report_operational_issue_without_recursive_dispatch(name):
    output = run_reporter(runs=[{"name": name}, {"name": name}])
    assert not calls_of(output, "dispatch")
    assert output["issues"][0]["labels"] == ["agentic-operations"]
    assert "<!-- neurodesktop-agentic-operations -->" in output["issues"][0]["body"]
    assert len(output["comments"]) == 1


@pytest.mark.parametrize("override", [
    {"status": "in_progress"}, {"conclusion": "success"},
    {"conclusion": "cancelled"}, {"name": "Report workflow failure"},
    {"name": "Unrecognized workflow"},
])
def test_non_failures_and_unlisted_workflows_do_nothing(override):
    output = run_reporter(runs=[override])
    assert output["results"] == [{"skipped": True}]
    assert not output["calls"]


@pytest.mark.parametrize("override", [
    {"id": "1234"}, {"run_attempt": -1},
    {"repository": {"full_name": "attacker/another-repository"}},
])
def test_invalid_run_identity_fails_before_any_api_access(override):
    output = run_reporter(runs=[override])
    assert "error" in output["results"][0]
    assert not output["calls"]


def test_untrusted_issue_and_comment_markers_cannot_suppress_reporting():
    output = run_reporter(
        issues=[marker_issue(user="contributor")],
        comments=[marker_comment("dispatched", user="contributor", issue=101)],
    )
    assert len(output["issues"]) == 2
    assert output["results"][0]["issue"] == 101
    assert len(calls_of(output, "dispatch")) == 1


def test_untrusted_comment_on_authoritative_issue_cannot_suppress_dispatch():
    output = run_reporter(
        issues=[marker_issue()],
        comments=[marker_comment("dispatched", user="contributor")],
    )
    assert len(output["issues"]) == 1
    assert len(calls_of(output, "dispatch")) == 1


def test_closed_authoritative_issue_is_reopened_and_reused():
    output = run_reporter(issues=[marker_issue(state="closed")])
    assert len(output["issues"]) == 1
    assert output["issues"][0]["state"] == "open"
    assert calls_of(output, "dispatch")[0]["inputs"] == {"issue-number": "42"}


def test_disabled_repair_records_evidence_without_consuming_dispatch_marker():
    output = run_reporter(repairEnabled=False)
    assert len(output["issues"]) == 1
    assert calls_of(output, "comment")
    assert not calls_of(output, "dispatch")
    assert not any("failure-dispatched:" in c["body"] for c in output["comments"])
    retried = run_reporter(runs=[{"repairEnabled": False}, {"repairEnabled": True}])
    assert len(retried["issues"]) == 1
    assert len(calls_of(retried, "dispatch")) == 1


def test_fork_failure_cannot_bypass_issue_admission():
    output = run_reporter(runs=[{"head_repository": {"full_name": "outsider/neurodesktop"}}])
    assert len(output["issues"]) == 1
    assert not calls_of(output, "dispatch")
    assert not any("failure-dispatched:" in c["body"] for c in output["comments"])


def test_workflow_subscribes_to_script_catalog_and_uses_trusted_checkout():
    workflow = WORKFLOW.read_text()
    catalog = run_reporter(runs=[])['workflows']
    subscription = workflow.split("    workflows:\n", 1)[1].split("    types:", 1)[0]
    assert {line.strip()[2:] for line in subscription.splitlines() if line.strip()} == set(catalog)
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "group: failure-report-${{ github.event.workflow_run.id }}" in workflow
    assert "head_sha" not in workflow
    assert "download-artifact" not in workflow
