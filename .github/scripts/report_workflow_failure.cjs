const { createHash } = require("node:crypto");

const OPERATIONAL_WORKFLOWS = new Set([
  "Agentic issue repair",
  "Agentic maintenance",
  "Agentic PR review",
  "Agentic runner readiness",
]);

const WORKFLOWS = new Set([
  "Build neurodesktop",
  "Build and publish Kasm image",
  "Build neurodesktop-test",
  "Build neurodesktop-dev",
  "Backfill neurodesktop history",
  "Unit tests",
  "Code quality",
  "PR image acceptance",
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

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function scopeOf(run) {
  if (!Number.isSafeInteger(run.workflow_id) || run.workflow_id <= 0 ||
      !/^[\w.-]+\/[\w.-]+$/.test(run.head_repository?.full_name || "") ||
      typeof run.head_branch !== "string" || !run.head_branch ||
      !/^[a-f0-9]{40}$/.test(run.head_sha || "")) return null;
  const scope = {
    workflowId: run.workflow_id, headRepository: run.head_repository.full_name.toLowerCase(),
    exactRef: run.head_branch,
  };
  if (run.event === "pull_request" || run.event === "pull_request_target") {
    if (!Array.isArray(run.pull_requests) || !run.pull_requests.length ||
        run.pull_requests.some((pr) => !Number.isSafeInteger(pr.number) || pr.number <= 0)) return null;
    scope.prNumbers = [...new Set(run.pull_requests.map((pr) => pr.number))].sort((a, b) => a - b);
  } else {
    if (!["push", "schedule", "workflow_dispatch", "repository_dispatch"].includes(run.event) ||
        !Array.isArray(run.pull_requests) || run.pull_requests.length) return null;
    scope.branchScope = true;
  }
  return scope;
}

function recordLine(kind, value) {
  return `<!-- neurodesktop-${kind}:v1 ${JSON.stringify(value).replace(/</g, "\\u003c").replace(/>/g, "\\u003e")} -->`;
}

function readRecord(item, kind) {
  if (item.user?.login !== BOT) return null;
  const line = item.body?.split("\n")[1] || "";
  const prefix = `<!-- neurodesktop-${kind}:v1 `;
  if (!line.startsWith(prefix) || !line.endsWith(" -->")) return null;
  try { return JSON.parse(line.slice(prefix.length, -4)); } catch { return null; }
}

function readEpisode(issue) {
  const first = issue.body?.split("\n")[0].match(/^<!-- neurodesktop-workflow-failure: run=(\d+) -->$/);
  if (!first) return null;
  const episode = readRecord(issue, "incident");
  if (!episode || !/^[a-f0-9]{64}$/.test(episode.key || "") ||
      episode.origin?.runId !== Number(first[1]) || !Number.isSafeInteger(episode.origin?.attempt) || episode.origin.attempt <= 0 ||
      !episode.bundle?.scope || !/^[a-f0-9]{40}$/.test(episode.bundle.sourceSHA || "") ||
      !Array.isArray(episode.bundle.failures) || !episode.bundle.failures.length ||
      episode.key !== digest(canonical(episode.bundle)) || !Array.isArray(episode.observations)) return null;
  if (episode.bundle.failures.some((failure) =>
    typeof failure.check?.jobName !== "string" || !Array.isArray(failure.check.failedStepNames) ||
    failure.check.failedStepNames.some((step) => typeof step !== "string") ||
    !FAILED.has(failure.conclusion) || typeof failure.diagnosis !== "string")) return null;
  if (episode.recovery && (!Number.isSafeInteger(episode.recovery.runId) || episode.recovery.runId <= 0 ||
      !Number.isSafeInteger(episode.recovery.attempt) || episode.recovery.attempt <= 0 ||
      !/^[a-f0-9]{40}$/.test(episode.recovery.sha || "") ||
      !["identical", "descendant"].includes(episode.recovery.relation) ||
      !Number.isFinite(episode.recovery.startedAt) || !Number.isFinite(episode.recovery.completedAt) ||
      episode.recovery.completedAt < episode.recovery.startedAt ||
      canonical(episode.recovery.covered) !== canonical(episode.observations) ||
      episode.observations.some((observation) => !observation.completedAt || observation.completedAt >= episode.recovery.startedAt))) return null;
  if (!episode.observations.length || episode.observations.some((observation) =>
    !Number.isSafeInteger(observation.runId) || observation.runId <= 0 ||
    !Number.isSafeInteger(observation.attempt) || observation.attempt <= 0 ||
    observation.sha !== episode.bundle.sourceSHA)) return null;
  return episode;
}

function observationOf(run, jobs) {
  const startedAt = Date.parse(run.run_started_at);
  const completed = jobs.map((job) => Date.parse(job.completed_at));
  return {
    runId: run.id, attempt: run.run_attempt, sha: run.head_sha,
    startedAt: Number.isFinite(startedAt) ? startedAt : null,
    completedAt: completed.length && completed.every(Number.isFinite) ? Math.max(...completed) : null,
  };
}

async function failureBundle(github, params, run, failedJobs) {
  const scope = scopeOf(run);
  if (!scope || !failedJobs.length) return null;
  const failures = [];
  for (const job of failedJobs) {
    let diagnosis = `opaque:${run.id}/${run.run_attempt}/${job.id}`;
    try {
      const response = await github.rest.actions.downloadJobLogsForWorkflowRun({
        ...params, job_id: job.id, request: { timeout: 10000 },
      });
      const log = typeof response.data === "string" ? response.data : Buffer.from(response.data).toString("utf8");
      // Hash the complete log, not a prefix or a generic exit-code annotation.
      if (Buffer.byteLength(log) <= 1024 * 1024) {
        const normalized = log.replace(/\x1b\[[0-9;]*m/g, "")
          .replace(/^\d{4}-\d\d-\d\dT[\d:.]+Z\s*/gm, "").trim();
        const substantive = normalized.replace(/^.*(?:Process completed with exit code|process .*failed with exit code).*$/gim, "");
        if (/CVE-\d{4}-\d+|FAILED .+|(?:Error|Exception|ERROR|FATAL|error):? .+/m.test(substantive)) {
          diagnosis = digest(normalized);
        }
      }
    } catch { /* Unavailable diagnostics remain specific to this occurrence. */ }
    failures.push({
      check: {jobName: String(job.name), failedStepNames: (job.steps || [])
        .filter((step) => FAILED.has(step.conclusion)).map((step) => step.name).sort()},
      conclusion: job.conclusion, diagnosis,
    });
  }
  failures.sort((a, b) => canonical(a).localeCompare(canonical(b)));
  const bundle = {scope, sourceSHA: run.head_sha, failures};
  // An oversized identity cannot be safely represented by a partial manifest.
  return recordLine("incident", {bundle}).length < 40000 ? bundle : null;
}

function covers(success, jobs, episode) {
  if (success.status !== "completed" || success.conclusion !== "success" ||
      canonical(scopeOf(success)) !== canonical(episode.bundle.scope)) return false;
  const observation = observationOf(success, jobs);
  if (!observation.startedAt || !observation.completedAt || observation.completedAt < observation.startedAt ||
      episode.observations.some((failure) => !failure.completedAt || observation.startedAt <= failure.completedAt ||
        (failure.runId === success.id && success.run_attempt <= failure.attempt))) return false;
  const names = episode.bundle.failures.map((failure) => failure.check.jobName);
  if (new Set(names).size !== names.length) return false;
  return episode.bundle.failures.every(({check}) => {
    const matches = jobs.filter((job) => job.name === check.jobName);
    if (matches.length !== 1) return false;
    const job = matches[0];
    if (job.status !== "completed" || job.conclusion !== "success" ||
        !Number.isFinite(Date.parse(job.started_at)) || !Number.isFinite(Date.parse(job.completed_at)) ||
        Date.parse(job.started_at) < observation.startedAt || Date.parse(job.completed_at) < Date.parse(job.started_at) ||
        new Set(check.failedStepNames).size !== check.failedStepNames.length) return false;
    return check.failedStepNames.every((name) => {
      const steps = (job.steps || []).filter((step) => step.name === name);
      return steps.length === 1 && steps[0].conclusion === "success";
    });
  });
}

async function saveEpisode(github, params, issue, episode) {
  const lines = issue.body.split("\n");
  if (lines[1]?.startsWith("<!-- neurodesktop-incident:v1 ")) lines[1] = recordLine("incident", episode);
  else lines.splice(1, 0, recordLine("incident", episode));
  const body = lines.join("\n");
  if (body.length > 60000) throw new Error("Incident record capacity exceeded; retaining existing evidence without dispatch or closure");
  await github.rest.issues.update({...params, issue_number: issue.number, body});
  issue.body = body;
}

async function recover(github, params, issue, episode, candidates) {
  if (!episode.recovery) {
    for (const candidate of candidates) {
      let success, jobs;
      try {
        success = (await github.rest.actions.getWorkflowRunAttempt({
          ...params, run_id: candidate.id, attempt_number: candidate.run_attempt,
        })).data;
        if (success.id !== candidate.id || success.run_attempt !== candidate.run_attempt ||
            success.repository?.full_name?.toLowerCase() !== `${params.owner}/${params.repo}`.toLowerCase()) continue;
        jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt, {
          ...params, run_id: success.id, attempt_number: success.run_attempt, per_page: 100,
        });
        if (!covers(success, jobs, episode)) continue;
        if (success.head_sha !== episode.bundle.sourceSHA) {
          const comparison = (await github.rest.repos.compareCommitsWithBasehead({
            ...params, basehead: `${episode.bundle.sourceSHA}...${success.head_sha}`,
          })).data;
          if (comparison.status !== "ahead") continue;
        }
      } catch { continue; }
      episode.recovery = {
        ...observationOf(success, jobs), covered: episode.observations.map((item) => ({...item})),
        relation: success.head_sha === episode.bundle.sourceSHA ? "identical" : "descendant",
      };
      await saveEpisode(github, params, issue, episode);
      break;
    }
  }
  if (!episode.recovery) return false;
  const proof = episode.recovery;
  const marker = `<!-- neurodesktop-workflow-recovery: run=${proof.runId} attempt=${proof.attempt} -->`;
  const comments = await github.paginate(github.rest.issues.listComments, {...params, issue_number: issue.number, per_page: 100});
  if (!comments.some((comment) => ownsMarker(comment, marker))) {
    await github.rest.issues.createComment({...params, issue_number: issue.number,
      body: `${marker}\n${recordLine("recovery", {key: episode.key, ...proof})}\nOriginal failed checks passed: https://github.com/${params.owner}/${params.repo}/actions/runs/${proof.runId}/attempts/${proof.attempt}`});
  }
  if (issue.state !== "closed") {
    await github.rest.issues.update({...params, issue_number: issue.number, state: "closed", state_reason: "completed"});
    issue.state = "closed";
  }
  return true;
}

async function recentSuccesses(github, params, run, observations) {
  if (!scopeOf(run) || !observations.every((item) => item.startedAt)) return {runs: [], incomplete: true};
  try {
    const {data} = await github.rest.actions.listWorkflowRuns({
      ...params, workflow_id: run.workflow_id, branch: run.head_branch,
      status: "completed", created: `>=${new Date(Math.min(...observations.map((item) => item.startedAt))).toISOString()}`,
      per_page: 100, page: 1,
    });
    if (!Array.isArray(data.workflow_runs) || !Number.isSafeInteger(data.total_count) ||
        data.total_count > data.workflow_runs.length || data.workflow_runs.length > 100) return {runs: [], incomplete: true};
    const candidates = new Map(data.workflow_runs.map((candidate) => [candidate.id, candidate]));
    // Reruns retain their original creation time, so query known runs directly.
    const known = [...new Set(observations.map((item) => item.runId))];
    if (known.length > 10) return {runs: [], incomplete: true};
    for (const runId of known) {
      const latest = (await github.rest.actions.getWorkflowRun({...params, run_id: runId})).data;
      if (!latest || latest.id !== runId) return {runs: [], incomplete: true};
      candidates.set(runId, latest);
    }
    const scoped = [...candidates.values()].filter((candidate) => canonical(scopeOf(candidate)) === canonical(scopeOf(run)));
    if (scoped.some((candidate) => !Number.isSafeInteger(candidate.run_attempt) || candidate.run_attempt < 1) ||
        scoped.reduce((count, candidate) => count + candidate.run_attempt, 0) > 10) return {runs: [], incomplete: true};
    const runs = [];
    for (const candidate of scoped) {
      for (let attempt = 1; attempt <= candidate.run_attempt; attempt++) {
        const exact = (await github.rest.actions.getWorkflowRunAttempt({...params, run_id: candidate.id, attempt_number: attempt})).data;
        if (exact.id !== candidate.id || exact.run_attempt !== attempt) return {runs: [], incomplete: true};
        if (exact.status === "completed" && exact.conclusion === "success") runs.push(exact);
      }
    }
    runs.sort((a, b) => Date.parse(a.run_started_at) - Date.parse(b.run_started_at));
    return {runs, incomplete: false};
  } catch { return {runs: [], incomplete: true}; }
}

async function adoptLegacy(github, params, issue, run, bundle, observation, comments) {
  if (!bundle || issue.state !== "open") return null;
  const attempts = new Set([run.run_attempt]);
  for (const comment of comments) {
    if (comment.user?.login !== BOT) continue;
    const match = comment.body?.split("\n")[0].match(/^<!-- neurodesktop-workflow-failure-attempt: run=(\d+) attempt=(\d+) -->$/);
    if (match && Number(match[1]) === run.id) attempts.add(Number(match[2]));
  }
  if (attempts.size > 10) return null;
  const observations = [observation];
  for (const attempt of attempts) {
    if (attempt === run.run_attempt) continue;
    try {
      const prior = (await github.rest.actions.getWorkflowRunAttempt({...params, run_id: run.id, attempt_number: attempt})).data;
      if (prior.id !== run.id || prior.run_attempt !== attempt || !FAILED.has(prior.conclusion)) return null;
      const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt, {
        ...params, run_id: run.id, attempt_number: attempt, per_page: 100,
      });
      const priorBundle = await failureBundle(github, params, prior, jobs.filter((job) => FAILED.has(job.conclusion)));
      if (canonical(priorBundle) !== canonical(bundle)) return null;
      observations.push(observationOf(prior, jobs));
    } catch { return null; }
  }
  const episode = {key: digest(canonical(bundle)), origin: {runId: run.id, attempt: Math.min(...attempts)}, bundle, observations};
  await saveEpisode(github, params, issue, episode);
  return episode;
}

function coveringPull(pr, issue, repository) {
  return pr.state === "open" && pr.user?.login === BOT &&
    pr.head?.repo?.full_name?.toLowerCase() === repository.toLowerCase() &&
    pr.body?.split("\n")[0] === `<!-- neurodesktop-agentic:issue:${issue.number} -->`;
}

async function reportWorkflowFailure({ github, context, core, repairEnabled = true }) {
  const run = context.payload.workflow_run;
  if (
    !run || run.status !== "completed" || (!FAILED.has(run.conclusion) && run.conclusion !== "success") ||
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

  const params = {owner, repo};
  const issues = await github.paginate(github.rest.issues.listForRepo, {
    ...params, state: "all", creator: BOT, per_page: 100,
  });
  const episodes = issues.filter((item) => !item.pull_request).map((issue) => ({issue, episode: readEpisode(issue)}))
    .filter((item) => item.episode);
  if (run.conclusion === "success") {
    const recovered = [];
    for (const {issue, episode} of episodes) {
      if (issue.state === "open" && canonical(episode.bundle.scope) === canonical(scopeOf(run)) &&
          await recover(github, params, issue, episode, [run])) recovered.push(issue.number);
    }
    return {recovered};
  }
  const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt, {
    owner, repo, run_id: run.id, attempt_number: run.run_attempt, per_page: 100,
  });
  const failedJobs = jobs.filter((job) => FAILED.has(job.conclusion));
  const cancelledJobs = failedJobs.length ? [] : jobs.filter((job) => job.conclusion === "cancelled");
  const cancelledWithoutSteps = cancelledJobs.length > 0 && cancelledJobs.every((job) => !job.steps?.length);
  const operational = OPERATIONAL_WORKFLOWS.has(run.name);
  const trustedHead = run.head_repository?.full_name?.toLowerCase() === repository.toLowerCase();
  const defaultBranchRun = run.head_branch === ref;
  let repairNote = "The completed run's failing jobs are recorded below. Automatic issue repair is dispatched explicitly because GITHUB_TOKEN-created issues do not trigger issue-opened workflows.";
  if (!repairEnabled) repairNote = "Automatic repair is not enabled. After runner setup, manually dispatch repair for this issue.";
  if (!trustedHead) repairNote = "This failure came from an untrusted or unavailable head repository. A maintainer must add agentic-approved or manually dispatch repair.";
  if (cancelledWithoutSteps) repairNote = "Cancelled jobs have no recorded steps. Automatic repair is skipped; inspect the run summary before re-running the workflow.";
  if (!defaultBranchRun) repairNote = "This feature-branch failure is recorded without automatic repair because issue repair edits the default branch.";
  if (operational) repairNote = "Agent infrastructure needs attention. Automatic repair is disabled for this report to prevent recursive agent runs.";
  const issueMarker = `<!-- neurodesktop-workflow-failure: run=${run.id} -->`;
  const attemptMarker = `<!-- neurodesktop-workflow-failure-attempt: run=${run.id} attempt=${run.run_attempt} -->`;
  const dispatchMarker = `<!-- neurodesktop-workflow-failure-dispatched: run=${run.id} attempt=${run.run_attempt} -->`;
  const runUrl = `${context.serverUrl || "https://github.com"}/${repository}/actions/runs/${run.id}`;
  const bundle = await failureBundle(github, params, run, failedJobs);
  const key = bundle && digest(canonical(bundle));
  const observation = observationOf(run, jobs);
  // Ownership precedes signature lookup, including closed historical episodes.
  let selected = episodes.find(({episode}) => episode.observations.some((item) =>
    item.runId === run.id && item.attempt === run.run_attempt));
  const related = selected ? selected.episode.observations : episodes
    .filter(({episode, issue}) => episode.key === key && issue.state === "open")
    .flatMap(({episode}) => episode.observations);
  const history = selected?.episode.recovery || !bundle ? {runs: [], incomplete: false}
    : await recentSuccesses(github, params, run, [...related, observation]);
  if (!selected && bundle) {
    for (const entry of episodes.filter(({episode, issue}) => episode.key === key && issue.state === "open")) {
      if (!await recover(github, params, entry.issue, entry.episode, history.runs)) { selected = entry; break; }
    }
  }
  let issue = selected?.issue || issues.filter((item) => !item.pull_request && ownsMarker(item, issueMarker) && !item.body.split("\n")[1]?.startsWith("<!-- neurodesktop-incident:"))
    .sort((a, b) => a.number - b.number)[0];
  let episode = selected?.episode;
  const newObservation = !episode?.observations.some((item) => item.runId === run.id && item.attempt === run.run_attempt);
  if (!issue && bundle) episode = {key, origin: {runId: run.id, attempt: run.run_attempt}, bundle, observations: [observation]};

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
        ...(episode ? [recordLine("incident", episode)] : []),
        ...(operational ? ["<!-- neurodesktop-agentic-operations -->"] : []),
        `Workflow **${inline(run.name)}** completed with \`${run.conclusion}\`.`,
        "", runUrl, "",
        `Event: \`${inline(run.event)}\`; branch: \`${inline(run.head_branch)}\`; commit: \`${inline(run.head_sha)}\`.`,
        ...(pullRequests ? [`Related pull requests: ${pullRequests}.`] : []),
        "",
        repairNote,
      ].join("\n"),
    })).data;
  } else if (episode && newObservation) {
    episode.observations.push(observation);
    await saveEpisode(github, params, issue, episode);
  }
  if (episode?.recovery) {
    await recover(github, params, issue, episode, []);
    return {issue: issue.number, dispatched: false, recovered: true};
  }
  if (issue.state === "closed") return {issue: issue.number, dispatched: false};

  const comments = await github.paginate(github.rest.issues.listComments, {
    owner, repo, issue_number: issue.number, per_page: 100,
  });
  if (!episode && bundle) episode = await adoptLegacy(github, params, issue, run, bundle, observation, comments);
  if (!comments.some((comment) => ownsMarker(comment, attemptMarker))) {
    const jobLines = [...failedJobs, ...cancelledJobs].map((job) => {
      const steps = (job.steps || []).filter((step) => FAILED.has(step.conclusion))
        .map((step) => inline(step.name)).join(", ");
      const notStarted = job.conclusion === "cancelled" && !job.steps?.length;
      const detail = steps ? `; steps: ${steps}` : notStarted ? "; no steps recorded" : "";
      return `- [${inline(job.name)}](${runUrl}/job/${job.id}): \`${job.conclusion}\`${detail}`;
    });
    let details = jobLines.join("\n");
    if (details.length > 50000) {
      details = `${details.slice(0, 50000)}\n\nJob detail exceeds the issue comment limit; see the complete run above.`;
    }
    await github.rest.issues.createComment({
      owner, repo, issue_number: issue.number,
      body: [
        attemptMarker,
        ...(episode ? [recordLine("observation", {key: episode.key, ...observation})] : []),
        cancelledJobs.length
          ? `Attempt ${run.run_attempt}: no job failed; ${cancelledJobs.length} cancelled jobs.`
          : `Attempt ${run.run_attempt}: ${failedJobs.length} failed or timed-out jobs.`,
        `${runUrl}/attempts/${run.run_attempt}`, "",
        details || "No failed job details are available. Inspect the workflow-level failure in the run summary.",
        ...(history.incomplete ? ["Recovery history is incomplete; this report remains open pending verified coverage."] : []),
      ].join("\n"),
    });
  }

  if (episode && await recover(github, params, issue, episode, history.runs)) {
    return {issue: issue.number, dispatched: false, recovered: true};
  }
  const pulls = await github.paginate(github.rest.pulls.list, {...params, state: "open", per_page: 100});
  if (pulls.some((pr) => coveringPull(pr, issue, repository))) return {issue: issue.number, dispatched: false};

  if (operational || cancelledWithoutSteps || !repairEnabled || !trustedHead || !defaultBranchRun || comments.some((comment) => ownsMarker(comment, dispatchMarker))) {
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
