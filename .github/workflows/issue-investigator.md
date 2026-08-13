---
name: Issue Investigator
description: Diagnose new issues and publish an evidence-backed classification and next action.
labels: [automation, issue-triage]
on:
  issues:
    types: [opened]
  workflow_dispatch:
    inputs:
      issue-number:
        description: Issue number to re-check.
        required: true
        type: string
      retry-reason:
        description: Why this issue is being re-checked.
        required: false
        type: string
  roles: all

permissions:
  contents: read
  issues: read
  pull-requests: read
  actions: read

engine:
  id: codex
  model: ${{ vars.GH_AW_MODEL_AGENT_CODEX || vars.GH_AW_DEFAULT_MODEL_CODEX || 'neurodesk' }}
  args: ["-c", "features.multi_agent=false"]
  env:
    OPENAI_BASE_URL: "https://llm.neurodesk.org/openai"
    OPENAI_API_KEY: ${{ secrets.CODEX_API_KEY || secrets.OPENAI_API_KEY }}

models:
  providers:
    openai:
      models:
        neurodesk:
          cost:
            input: "3e-06"
            output: "1.5e-05"

strict: true
max-ai-credits: -1
# Diagnosis has completed in roughly 20 model invocations; keep a bounded
# margin for one formatting recovery and the mandatory terminal safe output.
max-turns: 30
max-turn-cache-misses: 2000
timeout-minutes: 40
pre-agent-steps:
  - name: Install provenance-aware agent timeout filter
    run: |
      detector_dir="${RUNNER_TEMP}/gh-aw/actions"
      mv "${detector_dir}/detect_agent_errors.cjs" \
        "${detector_dir}/detect_agent_errors.upstream.cjs"
      install -m 0644 .github/scripts/gh_aw_detect_agent_errors_wrapper.cjs \
        "${detector_dir}/detect_agent_errors.cjs"

imports:
  - uses: .github/workflows/shared/agentic-models.md
network:
  allowed:
    - defaults
    - github
    - python
    - node
    - containers
    - linux-distros
    - llm.neurodesk.org

tools:
  github:
    mode: gh-proxy
    toolsets: [default]

safe-outputs:
  threat-detection:
    engine: false
  add-comment:
    max: 1
    target: "*"
    issues: true
    pull-requests: false
    discussions: false
    hide-older-comments: true
  noop:
    report-as-issue: false
---

# Issue Investigator

## Task

Diagnose the issue for this run and publish the best evidence-backed root cause,
failure class, and next action. This is a read-only diagnosis phase. Do not edit
repository files, create a branch, implement a fix, or run broad validation.

For an `issues` event, use `${{ github.event.issue.number }}` as the issue
number. For a `workflow_dispatch` run, use
`${{ github.event.inputs.issue-number }}` and treat
`${{ github.event.inputs.retry-reason }}` as prior context.

Use the pre-authenticated shell `gh` CLI through the GitHub read proxy to read
the issue, comments, linked pull requests, exact workflow runs, jobs, and the
smallest owning repository files. Use `safeoutputs` for every GitHub write and
completion signal.

## Completion Guard

- Use the pre-authenticated shell `gh` CLI for GitHub reads and never for
  writes. Use `safeoutputs` for every GitHub write and completion signal.
- Work directly without sub-agents, progress narration, or a todo list.
- The run is complete only after exactly one safe-output tool call:
  `add_comment` or `noop`. Never finish with a plan, progress message,
  checklist, or ordinary assistant response.
- Publish the best supported partial conclusion if evidence is incomplete.
  Preserve the final six model invocations for formatting recovery and the
  terminal safe-output call.

## Evidence Collection Budget

- Use a maximum of 8 read commands. Batch related reads and prefer exact run,
  job, log, artifact, commit, and source evidence over broad history searches.
- For CI failures, read the issue body and comments, the exact workflow run and
  job summary, one representative failing job log, and the smallest owning
  workflow or script file. If matrix failures disagree, read at most 2
  representative failing job logs.
- For matrix CI failures, do not inspect every matrix entry. Classify the
  failure from the common pattern and name the sampled jobs in the output.
- Use a maximum of 2 live network probes. Do not retry a failing read or probe
  more than once. A setup download failure is infrastructure evidence, not an
  agent failure.
- If a read or probe budget is reached, call a safe-output tool immediately.

## Hard Output Deadline

- The eighth read command is a hard decision deadline. Do not start another
  hypothesis after it.
- Call the selected safe-output tool before turn 24. A supported partial
  conclusion is better than exhausting the hard model-invocation ceiling.

## Classification and Output

Classify the issue as exactly one of: `repository defect`, `transient
infrastructure/setup failure`, `productive run exhausted its model-invocation
budget`, `runaway/retry loop`, `needs clarification`, or `duplicate`.

Use `add_comment` with:

1. the classification and concise root cause;
2. exact evidence, including run/job identifiers and the last productive action;
3. whether any agent actually started;
4. the smallest recommended next action; and
5. for a repository defect, instructions to manually dispatch `Issue Fixer`
   with this issue number after a human accepts the diagnosis.

Use `noop` only when an equivalent current diagnosis is already present and no
new evidence or next action would be added.
