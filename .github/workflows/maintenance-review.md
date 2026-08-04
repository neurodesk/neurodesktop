---
name: Weekly Maintenance Review Loop
description: Apply CodeRabbit feedback to draft pull requests created by weekly maintenance workflows.
labels: [automation, maintenance]
on:
  issue_comment:
    types: [created, edited]
  bots: ["coderabbitai[bot]"]

if: >-
  github.event.issue.pull_request != null &&
  github.event.issue.state == 'open' &&
  github.event.issue.user.login == 'github-actions[bot]' &&
  contains(github.event.issue.labels.*.name, 'agentic-workflow') &&
  startsWith(github.event.issue.title, '[maintenance] ') &&
  contains(github.event.comment.body, 'summarize by coderabbit.ai')

permissions:
  contents: read
  issues: read
  pull-requests: read
  actions: read

engine:
  id: codex
  model: ${{ vars.GH_AW_MODEL_AGENT_CODEX || vars.GH_AW_DEFAULT_MODEL_CODEX || 'neurodesk' }}
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
max-daily-ai-credits: -1
max-turn-cache-misses: 2000
timeout-minutes: 35
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
  push-to-pull-request-branch:
    target: triggering
    required-labels: [agentic-workflow]
    required-title-prefix: "[maintenance] "
    protected-files:
      policy: fallback-to-issue
      # These files are intentionally writable by one or more maintenance
      # categories. The originating PR still receives protected-file review.
      exclude:
        - "package.json"
        - "package-lock.json"
        - "pyproject.toml"
        - "README.md"
        - "AGENTS.md"
        - "CLAUDE.md"
        - ".github/actions/"
        - ".github/containerscan/"
    max: 1
    allowed-files:
      - "Dockerfile"
      - ".codespellrc"
      - ".dockerignore"
      - ".gitignore"
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
      - "config/**"
      - "docs/**"
      - "extensions/**"
      - "scripts/**"
      - "tests/**"
  reply-to-pull-request-review-comment:
    target: triggering
    required-labels: [agentic-workflow]
    required-title-prefix: "[maintenance] "
    max: 10
  add-comment:
    max: 1
    target: triggering
    issues: false
    pull-requests: true
  noop:
    report-as-issue: false
---

# Weekly Maintenance Review Loop

Review the open maintenance pull request that triggered this run and keep
iterating until no active actionable CodeRabbit findings remain.

Use `gh` through the GitHub tool to read the pull request metadata, diff, the
complete current CodeRabbit review, all CodeRabbit review comments, and their
thread state. The triggering summary comment is only a signal that the review
changed; it is not the complete review.

Before editing, verify that the pull request is open, belongs to this
repository, has the `agentic-workflow` label and `[maintenance] ` title prefix,
and was authored by `github-actions[bot]` (also exposed as
`app/github-actions`). Determine the maintenance category from the title and
keep all fixes within that original category and diff. Call `noop` if a guard
does not hold.

## Review Iteration

1. Collect every unresolved CodeRabbit finding from the latest completed review
   cycle, including findings summarized outside the diff.
2. Verify every finding against the current PR head. Fix only findings that are
   still valid. Reply to invalid or already-fixed inline findings with concise
   evidence.
3. Add or update focused regression tests when an appropriate seam exists. Do
   not expand the PR into a second maintenance task.
4. Run the validation required by `AGENTS.md` and `docs/testing.md` for the
   changed files. If it fails, do not push; add one PR comment with the exact
   failure and required follow-up.
5. Batch all validated fixes from the review cycle into one coherent commit and
   use `push-to-pull-request-branch` once.
6. After requesting the push, use `add-comment` with the body exactly
   `@coderabbitai review` to request the next incremental review.

If no active actionable findings remain, call `noop` with a concise reason. Do
not make speculative changes, request another review, mark the draft ready,
approve it, or merge it.
