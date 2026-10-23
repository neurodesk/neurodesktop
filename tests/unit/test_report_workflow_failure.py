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
let closeFailures = input.closeFailures || 0;
const attempts = new Map();
let current;
const bot = { login: 'github-actions[bot]' };
const rest = {
  actions: {
    listJobsForWorkflowRunAttempt() {},
    async getWorkflowRun(params) {
      calls.push({ type: 'attempt', ...params });
      const runs = [...attempts.values()].filter(run => run.id === params.run_id);
      return { data: runs.sort((a, b) => b.run_attempt - a.run_attempt)[0] };
    },
    async getWorkflowRunAttempt(params) {
      calls.push({ type: 'attempt', ...params });
      const run = attempts.get(`${params.run_id}/${params.attempt_number}`);
      if (!run) throw new Error('attempt unavailable');
      return { data: run };
    },
    async listWorkflowRuns(params) {
      calls.push({ type: 'history', ...params });
      if (input.historyError) throw new Error('history unavailable');
      const runs = input.history || [];
      return { data: { total_count: input.historyCount ?? runs.length, workflow_runs: runs } };
    },
    async downloadJobLogsForWorkflowRun(params) {
      calls.push({ type: 'log', ...params });
      if (current.logs?.[params.job_id] === null) throw new Error('log unavailable');
      return { data: current.logs?.[params.job_id] ?? input.logs?.[params.job_id] ?? '' };
    },
    async createWorkflowDispatch(params) {
      calls.push({ type: 'dispatch', ...params });
      if (dispatchFailures-- > 0) throw new Error('dispatch unavailable');
    },
  },
  repos: {
    async compareCommitsWithBasehead(params) {
      calls.push({ type: 'compare', ...params });
      return { data: { status: input.relation || 'diverged' } };
    },
  },
  pulls: { list() {} },
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
      if (params.state === 'closed' && closeFailures-- > 0) throw new Error('close unavailable');
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
    if (method === rest.actions.listJobsForWorkflowRunAttempt) {
      const run = attempts.get(`${params.run_id}/${params.attempt_number}`);
      return run?.jobs ?? input.jobs ?? [];
    }
    if (method === rest.pulls.list) return input.pulls || [];
    if (method === rest.issues.listForRepo) return issues;
    if (method === rest.issues.listComments) return comments.filter(c => c.issue_number === params.issue_number);
    throw new Error('Unexpected API method');
  },
};
(async () => {
  const results = [];
  const events = (input.runs || [{}]).map(override => ({
    id: 1234, run_attempt: 1, status: 'completed', conclusion: 'failure',
    workflow_id: 55, run_started_at: '2026-10-20T10:00:00Z', updated_at: '2026-10-20T10:10:00Z',
    name: 'Unit tests', repository: { full_name: 'NeuroDesk/neurodesktop' },
    event: 'pull_request', head_branch: 'main', head_sha: 'a'.repeat(40),
    head_repository: { full_name: 'NeuroDesk/neurodesktop' }, pull_requests: [{ number: 23 }],
    ...override,
  }));
  for (const run of [...events, ...(input.history || [])]) attempts.set(`${run.id}/${run.run_attempt}`, run);
  for (const override of events) {
    current = override;
    attempts.set(`${override.id}/${override.run_attempt}`, override);
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


@pytest.mark.parametrize("workflow", ["Unit tests", "Code quality"])
def test_completed_run_aggregates_matrix_failures_and_dispatches_default_branch(workflow):
    output = run_reporter(runs=[{"name": workflow}], jobs=[
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


@pytest.mark.parametrize("successful_jobs", [[], [
    {"id": 9, "name": "setup", "conclusion": "success", "steps": [
        {"name": "Prepare runner", "conclusion": "success"},
    ]},
]])
def test_cancelled_jobs_without_steps_are_reported_without_guessing_cause(successful_jobs):
    output = run_reporter(jobs=successful_jobs + [
        {"id": 7, "name": "Check for spelling errors", "conclusion": "cancelled", "steps": []},
        {"id": 8, "name": "lint", "conclusion": "skipped", "steps": []},
    ])
    body = output["issues"][0]["body"]
    assert "Cancelled jobs have no recorded steps" in body
    assert "did not assign a runner" not in body
    assert "No job started" not in body
    comment = output["comments"][0]["body"]
    assert "no job failed; 1 cancelled jobs" in comment
    assert "/actions/runs/1234/job/7): `cancelled`; no steps recorded" in comment
    assert "lint" not in comment
    assert not calls_of(output, "dispatch")
    assert output["results"][0]["dispatched"] is False


def test_cancelled_job_that_ran_steps_still_dispatches_repair():
    output = run_reporter(jobs=[
        {"id": 7, "name": "pytest", "conclusion": "cancelled", "steps": [
            {"name": "checkout", "conclusion": "success"},
            {"name": "Run unit tests", "conclusion": "cancelled"},
        ]},
    ])
    assert "did not assign a runner" not in output["issues"][0]["body"]
    comment = output["comments"][0]["body"]
    assert "/actions/runs/1234/job/7): `cancelled`\n" in comment + "\n"
    assert "never started" not in comment
    assert len(calls_of(output, "dispatch")) == 1


def test_cancelled_siblings_are_omitted_when_a_job_failed():
    output = run_reporter(jobs=[
        {"id": 1, "name": "amd64", "conclusion": "failure", "steps": []},
        {"id": 2, "name": "arm64", "conclusion": "cancelled", "steps": []},
    ])
    comment = output["comments"][0]["body"]
    assert "1 failed or timed-out jobs" in comment
    assert "arm64" not in comment and "never started" not in comment
    assert len(calls_of(output, "dispatch")) == 1


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
    assert [call["type"] for call in output["calls"] if call["type"] in {"issue", "comment", "update", "dispatch"}] == [
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
    {"status": "in_progress"},
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


def test_manually_closed_legacy_issue_is_not_reopened_or_treated_as_recovery():
    output = run_reporter(issues=[marker_issue(state="closed")])
    assert len(output["issues"]) == 1
    assert output["issues"][0]["state"] == "closed"
    assert not calls_of(output, "dispatch")


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
    assert "group: failure-report-${{ github.event.workflow_run.workflow_id }}" in workflow
    assert "queue: max" in workflow
    assert '["failure", "timed_out", "success"]' in workflow
    assert "pull-requests: read" in workflow
    assert "head_sha" not in workflow
    assert "download-artifact" not in workflow


def incident_run(number=1234, hour=10, *, conclusion="failure", **changes):
    start = f"2026-10-20T{hour:02d}:00:00Z"
    end = f"2026-10-20T{hour:02d}:10:00Z"
    jobs = [{
        "id": number * 10 + index, "name": name, "conclusion": conclusion,
        "status": "completed", "started_at": start, "completed_at": end,
        "steps": [{"name": "Check", "conclusion": conclusion}],
    } for index, name in enumerate(["scan (amd64)", "scan (arm64)", "runtime (arm64)"])]
    run = {
        "id": number, "workflow_id": 55, "run_attempt": 1, "status": "completed",
        "name": "Unit tests", "repository": {"full_name": "NeuroDesk/neurodesktop"},
        "head_repository": {"full_name": "NeuroDesk/neurodesktop"},
        "event": "push", "head_branch": "main", "head_sha": "a" * 40, "pull_requests": [],
        "conclusion": conclusion, "run_started_at": start, "updated_at": end, "jobs": jobs,
        "logs": {str(job["id"]): "CVE-2026-1111 libthing 1.0 HIGH fixed 1.1\n" if index < 2 else
                 "FAILED test_guac_vnc_tunnel[relative-xdg] - AssertionError: Guacamole protocol error\n"
                 for index, job in enumerate(jobs)},
    }
    run.update(changes)
    return run


def test_equivalent_failures_share_one_unresolved_incident_and_keep_all_occurrences():
    output = run_reporter(runs=[incident_run(), incident_run(1235, 11)])
    assert len(output["issues"]) == 1
    comments = "\n".join(comment["body"] for comment in output["comments"])
    assert "run=1234 attempt=1" in comments and "run=1235 attempt=1" in comments
    assert "/runs/1235/attempts/1" in comments


@pytest.mark.parametrize("change", [
    {"head_sha": "b" * 40}, {"head_branch": "other"}, {"workflow_id": 56},
    {"event": "pull_request", "pull_requests": [{"number": 2}]},
    {"head_repository": {"full_name": "fork/neurodesktop"}},
    {"workflow_id": None},
])
def test_failure_scope_and_revision_isolation(change):
    output = run_reporter(runs=[incident_run(), incident_run(1235, 11, **change)])
    assert len(output["issues"]) == 2


@pytest.mark.parametrize("diagnosis", ["CVE-2026-2222 libthing 1.0 HIGH fixed 1.2", "", None])
def test_changed_or_unavailable_diagnosis_does_not_group_same_steps(diagnosis):
    later = incident_run(1235, 11)
    later["logs"]["12350"] = diagnosis
    output = run_reporter(runs=[incident_run(), later])
    assert len(output["issues"]) == 2


def test_success_with_complete_coverage_closes_and_new_failure_is_a_new_episode():
    output = run_reporter(runs=[
        incident_run(), incident_run(1235, 11, conclusion="success"),
        incident_run(1236, 12), incident_run(),
    ])
    assert [issue["state"] for issue in output["issues"]] == ["closed", "open"]
    assert output["results"][3]["issue"] == output["issues"][0]["number"]
    assert len(calls_of(output, "dispatch")) == 2


@pytest.mark.parametrize("problem", ["missing", "cancelled", "skipped", "step", "duplicate", "older", "sha", "scope", "attempt"])
def test_success_without_complete_authoritative_coverage_keeps_issue_open(problem):
    green = incident_run(1235, 11, conclusion="success")
    if problem == "missing":
        green["jobs"].pop()
    elif problem in {"cancelled", "skipped"}:
        green["jobs"][-1]["conclusion"] = problem
    elif problem == "step":
        green["jobs"][-1]["steps"] = []
    elif problem == "duplicate":
        green["jobs"].append(green["jobs"][-1])
    elif problem == "older":
        green["run_started_at"] = "2026-10-20T09:00:00Z"
    elif problem == "sha":
        green["head_sha"] = "b" * 40
    elif problem == "scope":
        green["head_branch"] = "other"
    else:
        green["run_attempt"] = 0
    output = run_reporter(runs=[incident_run(), green])
    assert output["issues"][0]["state"] == "open"


def test_descendant_success_and_retry_after_failed_close_finish_recovery():
    green = incident_run(1235, 11, conclusion="success", head_sha="b" * 40)
    output = run_reporter(runs=[incident_run(), green, green], relation="ahead", closeFailures=1)
    assert output["results"][1] == {"error": "close unavailable"}
    assert output["issues"][0]["state"] == "closed"
    assert len([c for c in output["comments"] if "workflow-recovery:" in c["body"]]) == 1


def test_success_before_failure_delivery_is_reconciled_from_targeted_history():
    green = incident_run(1235, 11, conclusion="success")
    output = run_reporter(runs=[green, incident_run()], history=[green])
    assert output["issues"][0]["state"] == "closed"
    assert not calls_of(output, "dispatch")
    assert len(calls_of(output, "history")) <= 2


def test_delayed_green_separates_recurrence_before_attaching_later_failure():
    green = incident_run(1235, 11, conclusion="success")
    output = run_reporter(runs=[incident_run(), incident_run(1236, 12)], history=[green])
    assert [issue["state"] for issue in output["issues"]] == ["closed", "open"]


@pytest.mark.parametrize("history", [{"historyError": True}, {"historyCount": 101}])
def test_incomplete_history_fails_closed_with_explicit_notice(history):
    green = incident_run(1235, 11, conclusion="success")
    output = run_reporter(runs=[incident_run()], history=[green], **history)
    assert output["issues"][0]["state"] == "open"
    assert "incomplete" in json.dumps(output).lower()


@pytest.mark.parametrize("spoof", [None, "human", "fork", "quoted", "old"])
def test_only_owned_canonical_pr_suppresses_repair_after_recording_occurrence(spoof):
    pr = {
        "number": 500, "state": "open", "user": {"login": "github-actions[bot]"},
        "head": {"repo": {"full_name": "NeuroDesk/neurodesktop"}},
        "body": "<!-- neurodesktop-agentic:issue:100 -->\nExisting proposal",
    }
    if spoof == "human":
        pr["user"]["login"] = "maintainer"
    elif spoof == "fork":
        pr["head"]["repo"]["full_name"] = "fork/neurodesktop"
    elif spoof == "quoted":
        pr["body"] = "Quoted:\n" + pr["body"]
    elif spoof == "old":
        pr["body"] = "<!-- neurodesktop-agentic:issue:99 -->"
    output = run_reporter(runs=[incident_run(), incident_run(1235, 11)], pulls=[pr])
    assert len(output["issues"]) == 1
    assert any("run=1235 attempt=1" in c["body"] for c in output["comments"])
    assert len(calls_of(output, "dispatch")) == (0 if spoof is None else 2)


@pytest.mark.parametrize("binding", ["current", "stale", "malformed", "version"])
def test_explicit_pr_binding_must_match_current_episode(binding):
    first = run_reporter(runs=[incident_run()], repairEnabled=False)
    episode = json.loads(first["issues"][0]["body"].splitlines()[1].removeprefix("<!-- neurodesktop-incident:v1 ").removesuffix(" -->"))
    value = {"key": episode["key"], "origin": episode["origin"]}
    if binding == "stale":
        value["origin"] = {"runId": 999, "attempt": 1}
    record = json.dumps(value) if binding != "malformed" else "{invalid"
    version = "v2" if binding == "version" else "v1"
    pr = {
        "number": 500, "state": "open", "user": {"login": "github-actions[bot]"},
        "head": {"repo": {"full_name": "NeuroDesk/neurodesktop"}},
        "body": "<!-- neurodesktop-agentic:issue:100 -->\n"
                f"<!-- neurodesktop-incident-binding:{version} {record} -->",
    }
    output = run_reporter(issues=first["issues"], comments=first["comments"],
                          runs=[incident_run(1235, 11)], pulls=[pr])
    assert len(output["issues"]) == 1
    assert any("run=1235 attempt=1" in c["body"] for c in output["comments"])
    assert len(calls_of(output, "dispatch")) == (0 if binding == "current" else 1)


def test_legacy_exact_run_is_adopted_only_with_matching_authoritative_attempt():
    output = run_reporter(issues=[marker_issue()], runs=[incident_run(), incident_run(1235, 11)])
    assert len(output["issues"]) == 1
    assert output["issues"][0]["number"] == 42
    assert "neurodesktop-incident:v1" in output["issues"][0]["body"]


def test_successful_exact_attempt_retry_closes_failure():
    output = run_reporter(runs=[incident_run(), incident_run(hour=11, run_attempt=2, conclusion="success")])
    assert output["issues"][0]["state"] == "closed"


def test_incomplete_history_can_be_reconsidered_on_failure_redelivery():
    first = run_reporter(runs=[incident_run()], historyError=True)
    green = incident_run(1235, 11, conclusion="success")
    output = run_reporter(issues=first["issues"], comments=first["comments"], runs=[incident_run()], history=[green])
    assert output["issues"][0]["state"] == "closed"
    assert not calls_of(output, "dispatch")


@pytest.mark.parametrize("newer_first", [True, False])
@pytest.mark.parametrize("closed", [False, True])
def test_legacy_attempt_owner_precedes_equivalent_newer_episode(newer_first, closed):
    original = marker_issue(state="closed" if closed else "open")
    receipts = [marker_comment("attempt"), marker_comment("dispatched")]
    pr = {
        "number": 500, "state": "open", "user": {"login": "github-actions[bot]"},
        "head": {"repo": {"full_name": "NeuroDesk/neurodesktop"}},
        "body": "<!-- neurodesktop-agentic:issue:42 -->\nExisting proposal",
    }
    older, newer = incident_run(), incident_run(1235, 11)
    runs = [newer, older] if newer_first else [older, newer]
    output = run_reporter(issues=[original], comments=receipts, pulls=[pr], runs=runs + [older])
    assert output["results"][1 if newer_first else 0]["issue"] == 42
    assert output["results"][-1]["issue"] == 42
    assert output["results"][-1]["dispatched"] is False
    for kind in ("attempt", "dispatched"):
        marker = f"<!-- neurodesktop-workflow-failure-{kind}: run=1234 attempt=1 -->"
        owners = [c["issue_number"] for c in output["comments"] if c["body"].splitlines()[0] == marker]
        assert owners == [42]
    assert not any(c["inputs"]["issue-number"] == "42" for c in calls_of(output, "dispatch"))
    assert output["issues"][0]["state"] == ("closed" if closed else "open")


def test_missing_scope_never_groups_two_runs_or_authorizes_recovery():
    output = run_reporter(runs=[
        incident_run(workflow_id=None), incident_run(1235, 11, workflow_id=None),
        incident_run(1236, 12, conclusion="success", workflow_id=None),
    ])
    assert [issue["state"] for issue in output["issues"]] == ["open", "open"]


def test_quoted_or_user_episode_cannot_authorize_recovery():
    first = run_reporter(runs=[incident_run()])
    issue = first["issues"][0]
    for altered in [{**issue, "body": "Quotation\n" + issue["body"]},
                    {**issue, "user": {"login": "maintainer"}}]:
        output = run_reporter(issues=[altered], runs=[incident_run(1235, 11, conclusion="success")])
        assert output["issues"][0]["state"] == "open"


def test_complete_trivy_diagnosis_ignores_successful_setup_and_candidate_tags():
    diagnosis = repo_path("tests/fixtures/workflow-failures/trivy-arm64.log").read_text()
    first = incident_run(39641150130)
    first["jobs"] = first["jobs"][:1]
    first["logs"] = {str(first["jobs"][0]["id"]): "Worker ID: first\nPull image sha256:111\n" + diagnosis}
    later = incident_run(39820073728, 11)
    later["jobs"] = later["jobs"][:1]
    later["logs"] = {str(later["jobs"][0]["id"]): "Worker ID: second\nPull image sha256:222\n" + diagnosis.replace("39641150130", "39820073728")}
    output = run_reporter(runs=[first, later])
    assert len(output["issues"]) == 1


@pytest.mark.parametrize("change", [
    ("CVE-2026-98571", "CVE-2026-11111"), ("2026.10.19", "2026.10.20"),
    ("0.13.1", "0.13.2"), ("Total: 4", "Total: 5"),
    ("##[error]Process completed with exit code 1.", ""),
])
def test_trivy_diagnosis_changes_and_incomplete_reports_never_group(change):
    diagnosis = repo_path("tests/fixtures/workflow-failures/trivy-arm64.log").read_text()
    first = incident_run(39641150130)
    first["jobs"] = first["jobs"][:1]
    first["logs"] = {str(first["jobs"][0]["id"]): diagnosis}
    later = incident_run(39820073728, 11)
    later["jobs"] = later["jobs"][:1]
    later["logs"] = {str(later["jobs"][0]["id"]): diagnosis.replace("39641150130", "39820073728").replace(*change)}
    assert len(run_reporter(runs=[first, later])["issues"]) == 2


@pytest.mark.parametrize("same_error", [True, False])
def test_pytest_failure_sections_keep_assertions_and_ignore_setup(same_error):
    section = (
        "================ FAILURES ================\n"
        "________________ test_guac ________________\n"
        "E AssertionError: Guacamole protocol error\n"
        "================ short test summary info ================\n"
        "FAILED test_guac - AssertionError: Guacamole protocol error\n"
        "================ 1 failed in 1.00s ================\n"
    )
    first, later = incident_run(), incident_run(1235, 11)
    first["logs"]["12342"] = "Worker ID: one\n" + section
    later["logs"]["12352"] = "Worker ID: two\n" + section.replace("1.00s", "2.00s")
    if not same_error:
        later["logs"]["12352"] = later["logs"]["12352"].replace("Guacamole protocol error", "Connection refused")
    output = run_reporter(runs=[first, later])
    assert len(output["issues"]) == (1 if same_error else 2)
