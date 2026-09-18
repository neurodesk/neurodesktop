const OPERATIONAL_WORKFLOWS = new Set([
  "Agentic issue repair",
  "Agentic maintenance",
  "Agentic PR review",
  "Agentic runner readiness",
]);

const WORKFLOWS = new Set([
  "Build neurodesktop",
  "Build neurodesktop-test",
  "Build neurodesktop-dev",
  "Backfill neurodesktop history",
  "Unit tests",
  "Codespell",
  "Test Objectstorage",
  "Test cvmfs",
  "Self-hosted Runner Test",
  "JupyterHub API Testing (All instances)",
  "JupyterHub Notebook Testing (All instances)",
  ...OPERATIONAL_WORKFLOWS,
]);

const FAILED = new Set(["failure", "timed_out"]);
const BOT = "github-actions[bot]";

function ownsMarker(item, marker) {
  return item.user?.login === BOT && item.body?.split("\n")[0] === marker;
}

function inline(value) {
  const escaped = String(value)
    .replace(/\\/g, "\\\\")
    .replace(/[\[\]()]/g, "\\$&")
    .replace(/[\r\n`]/g, " ");
  let limited = escaped.slice(0, 300);
  const trailingBackslashes = limited.match(/\\+$/)?.[0].length || 0;
  if (trailingBackslashes % 2) limited = limited.slice(0, -1);
  return limited;
}

async function reportWorkflowFailure({ github, context, core, repairEnabled = true }) {
  const run = context.payload.workflow_run;
  if (
    !run || run.status !== "completed" || !FAILED.has(run.conclusion) ||
    !WORKFLOWS.has(run.name)
  ) {
    return { skipped: true };
  }

  const { owner, repo } = context.repo;
  const repository = `${owner}/${repo}`;
  if (run.repository?.full_name?.toLowerCase() !== repository.toLowerCase()) {
    throw new Error("Failure run does not belong to this repository");
  }
  if (!Number.isSafeInteger(run.id) || run.id <= 0 ||
      !Number.isSafeInteger(run.run_attempt) || run.run_attempt <= 0) {
    throw new Error("Failure run has an invalid run ID or attempt");
  }
  const ref = context.payload.repository?.default_branch;
  if (typeof ref !== "string" || !ref) {
    throw new Error("Repository default branch is missing");
  }

  const operational = OPERATIONAL_WORKFLOWS.has(run.name);
  const trustedHead = run.head_repository?.full_name?.toLowerCase() === repository.toLowerCase();
  const defaultBranchRun = run.head_branch === ref;
  let repairNote = "The completed run's failing jobs are recorded below. Automatic issue repair is dispatched explicitly because GITHUB_TOKEN-created issues do not trigger issue-opened workflows.";
  if (!repairEnabled) repairNote = "Automatic repair is not enabled. After runner setup, manually dispatch repair for this issue.";
  if (!trustedHead) repairNote = "This failure came from an untrusted or unavailable head repository. A maintainer must add agentic-approved or manually dispatch repair.";
  if (!defaultBranchRun) repairNote = "This feature-branch failure is recorded without automatic repair because issue repair edits the default branch.";
  if (operational) repairNote = "Agent infrastructure needs attention. Automatic repair is disabled for this report to prevent recursive agent runs.";
  const issueMarker = `<!-- neurodesktop-workflow-failure: run=${run.id} -->`;
  const attemptMarker = `<!-- neurodesktop-workflow-failure-attempt: run=${run.id} attempt=${run.run_attempt} -->`;
  const dispatchMarker = `<!-- neurodesktop-workflow-failure-dispatched: run=${run.id} attempt=${run.run_attempt} -->`;
  const runUrl = `${context.serverUrl || "https://github.com"}/${repository}/actions/runs/${run.id}`;
  const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt, {
    owner, repo, run_id: run.id, attempt_number: run.run_attempt, per_page: 100,
  });
  const failedJobs = jobs.filter((job) => FAILED.has(job.conclusion));
  const issues = await github.paginate(github.rest.issues.listForRepo, {
    owner, repo, state: "all", creator: BOT, per_page: 100,
  });
  let issue = issues.filter((item) => !item.pull_request && ownsMarker(item, issueMarker))
    .sort((a, b) => a.number - b.number)[0];

  if (!issue) {
    const pullRequests = (run.pull_requests || [])
      .filter((pr) => Number.isSafeInteger(pr.number) && pr.number > 0)
      .map((pr) => `#${pr.number}`).join(", ");
    issue = (await github.rest.issues.create({
      owner, repo,
      title: `${operational ? "Agent operation failure" : "Job failure"} - ${inline(run.name)} - run ${run.id}`,
      labels: operational ? ["agentic-operations"] : ["bug"],
      body: [
        issueMarker,
        ...(operational ? ["<!-- neurodesktop-agentic-operations -->"] : []),
        `Workflow **${inline(run.name)}** completed with \`${run.conclusion}\`.`,
        "", runUrl, "",
        `Event: \`${inline(run.event)}\`; branch: \`${inline(run.head_branch)}\`; commit: \`${inline(run.head_sha)}\`.`,
        ...(pullRequests ? [`Related pull requests: ${pullRequests}.`] : []),
        "",
        repairNote,
      ].join("\n"),
    })).data;
  } else if (issue.state === "closed") {
    await github.rest.issues.update({ owner, repo, issue_number: issue.number, state: "open" });
  }

  const comments = await github.paginate(github.rest.issues.listComments, {
    owner, repo, issue_number: issue.number, per_page: 100,
  });
  if (!comments.some((comment) => ownsMarker(comment, attemptMarker))) {
    const jobLines = failedJobs.map((job) => {
      const steps = (job.steps || []).filter((step) => FAILED.has(step.conclusion))
        .map((step) => inline(step.name)).join(", ");
      return `- [${inline(job.name)}](${runUrl}/job/${job.id}): \`${job.conclusion}\`${steps ? `; steps: ${steps}` : ""}`;
    });
    let details = jobLines.join("\n");
    if (details.length > 50000) {
      details = `${details.slice(0, 50000)}\n\nJob detail exceeds the issue comment limit; see the complete run above.`;
    }
    await github.rest.issues.createComment({
      owner, repo, issue_number: issue.number,
      body: [
        attemptMarker,
        `Attempt ${run.run_attempt}: ${failedJobs.length} failed or timed-out jobs.`,
        `${runUrl}/attempts/${run.run_attempt}`, "",
        details || "No failed job details are available. Inspect the workflow-level failure in the run summary.",
      ].join("\n"),
    });
  }

  if (operational || !repairEnabled || !trustedHead || !defaultBranchRun || comments.some((comment) => ownsMarker(comment, dispatchMarker))) {
    return { issue: issue.number, dispatched: false, operational };
  }

  await github.rest.actions.createWorkflowDispatch({
    owner, repo, workflow_id: "agentic-issue.yml", ref,
    inputs: { "issue-number": String(issue.number) },
  });
  // A crash here can repeat delivery; issue repair must reuse its existing PR.
  await github.rest.issues.createComment({
    owner, repo, issue_number: issue.number,
    body: `${dispatchMarker}\nDispatched automatic issue repair for attempt ${run.run_attempt}.`,
  });
  core.info(`Reported run ${run.id}, attempt ${run.run_attempt}, on issue #${issue.number}.`);
  return { issue: issue.number, dispatched: true, operational };
}

module.exports = { reportWorkflowFailure, WORKFLOWS, OPERATIONAL_WORKFLOWS };
