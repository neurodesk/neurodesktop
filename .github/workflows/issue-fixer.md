---
name: Issue Fixer
description: Implement and validate a focused fix for an accepted issue diagnosis.
labels: [automation, issue-fix]
on:
  workflow_dispatch:
    inputs:
      issue-number:
        description: Issue number whose accepted diagnosis should be fixed.
        required: true
        type: string
      diagnosis-context:
        description: Optional diagnosis comment URL or reviewer guidance.
        required: false
        type: string

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
# Productive issue-fix transcripts reached the old 40-invocation limit after
# editing and before validation. This remains a hard ceiling above that size.
max-turns: 80
max-turn-cache-misses: 2000
timeout-minutes: 90
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
  create-pull-request:
    title-prefix: "[issue-investigator] "
    branch-prefix: "agentic/issue-"
    labels: [agentic-workflow]
    draft: true
    auto-close-issue: true
    protected-files: request_review
    max-patch-files: 30
    allowed-files:
      - "Dockerfile"
      - ".codespellrc"
      - ".dockerignore"
      - ".trivyignore.yaml"
      - "AGENTS.md"
      - "CLAUDE.md"
      - "README.md"
      - "build_and_run.bat"
      - "build_and_run.sh"
      - "neurodesk.yml"
      - "stop_and_clean.bat"
      - "stop_and_clean.sh"
      - ".github/actions/**"
      - ".github/containerscan/**"
      - ".github/*_template.md"
      - "config/**"
      - "docs/**"
      - "extensions/**"
      - "scripts/**"
      - "tests/**"
  noop:
    report-as-issue: false
---

# Issue Fixer

## Task

Implement the smallest coherent repository fix for issue
`${{ github.event.inputs.issue-number }}` after reading the issue, its complete
current comments, and `${{ github.event.inputs.diagnosis-context }}` as optional
reviewer context. Re-check the diagnosis against the current default branch;
accepted diagnosis is a starting point, not permission to preserve a stale
conclusion.

Use the pre-authenticated shell `gh` CLI only for reads and `safeoutputs` for
every GitHub write and completion signal. Work directly without sub-agents,
progress narration, or a todo list.

## Completion Guard

- The run is complete only after exactly one safe-output call:
  `create_pull_request`, `add_comment`, or `noop`.
- Start the terminal-output phase by turn 70 and call the selected safe-output
  tool before turn 72. The remaining eight model invocations are recovery
  margin, not an invitation to start another hypothesis.
- If validation cannot finish, do not submit an unvalidated patch. Revert the
  incomplete candidate when practical and use `add_comment` with the exact
  blocker and preserved evidence.

## Fix Contract

1. Confirm the reported behavior and owning subsystem with the smallest
   relevant reads or reproduction. Do not repeat the broad investigation.
2. Read `AGENTS.md` and the directly relevant reference documentation before
   editing governed behavior.
3. Add or update focused regression tests, then make the smallest change that
   fixes the proven cause. Avoid unrelated refactors.
4. Run the focused tests required by `AGENTS.md` and `docs/testing.md`. Do not
   hide command failures in a pipeline; enable `pipefail` if a pipeline is
   unavoidable.
5. Review the final diff for secrets, generated debris, unrelated edits, and
   uncompiled agentic workflow sources.

Use `create_pull_request` only for a validated fix. Keep the existing
`[issue-investigator] ` title prefix so the established CodeRabbit review loop
continues to own these draft PRs. Include the issue number, root cause, exact
validation, and any validation that remains external in the PR body.

Use `add_comment` when the accepted diagnosis is stale, the fix is outside the
allowed file scope, a human or infrastructure decision is required, or focused
validation cannot complete. Use `noop` only if the issue is already fixed or a
current competing PR makes another change unnecessary.
