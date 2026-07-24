---
name: Issue Investigator Review Loop
description: Apply CodeRabbit feedback to draft pull requests created by the issue investigator.
labels: [automation, issue-triage]
on:
  issue_comment:
    types: [created, edited]
  bots: ["coderabbitai[bot]"]

if: >-
  github.event.issue.pull_request != null &&
  github.event.issue.state == 'open' &&
  github.event.issue.user.login == 'github-actions[bot]' &&
  contains(github.event.issue.labels.*.name, 'agentic-workflow') &&
  startsWith(github.event.issue.title, '[issue-investigator] ') &&
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
# The runtime AIC catalog does not consume custom model pricing overlays yet.
# Retain the pricing above for reporting, but disable enforcement for this alias.
max-ai-credits: -1
max-turn-cache-misses: 2000
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
    required-title-prefix: "[issue-investigator] "
    protected-files: fallback-to-issue
    max: 1
    allowed-files:
      - "Dockerfile"
      - ".codespellrc"
      - ".dockerignore"
      - ".trivyignore.yaml"
      - "AGENTS.md"
      - "CLAUDE.md"
      - "README.md"
      - "analyze_image_size.sh"
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
  reply-to-pull-request-review-comment:
    target: triggering
    required-labels: [agentic-workflow]
    required-title-prefix: "[issue-investigator] "
    max: 10
  add-comment:
    max: 1
    target: triggering
    issues: false
    pull-requests: true
  noop:
    report-as-issue: false
---

# Issue Investigator Review Loop

## Task

Review the open issue-investigator pull request that triggered this run and keep
iterating on it until no active actionable CodeRabbit findings remain.

Use `gh` through the GitHub tool to read the pull request metadata, diff, the
complete current CodeRabbit review, all CodeRabbit review comments, and the
state of their review threads. Do not treat the triggering comment as the complete review;
it is only the signal that CodeRabbit updated its review.

Before changing anything, verify that the pull request is still open, is in this
repository, has the `agentic-workflow` label and `[issue-investigator] ` title
prefix, and was authored by the GitHub Actions bot (`github-actions[bot]`, also
shown as `app/github-actions` by some `gh` output). Call `noop` if any guard no
longer holds.

## Review Iteration

1. Collect every unresolved CodeRabbit finding from the latest completed review
   cycle, including findings summarized outside the diff.
2. Verify every finding against the current PR head. Fix only findings that are
   still valid. Reply to an invalid or already-fixed inline finding with concise
   evidence instead of changing code for it.
3. Add or update focused regression tests for valid findings when an appropriate
   test seam exists. Keep the changes within the original PR's scope and the
   configured allowed files.
4. Run the smallest relevant tests for the changed files. Do not push if those
   tests fail; instead add one PR comment that reports the exact failure and
   required follow-up.
5. Batch all validated fixes from the review cycle into one coherent commit and
   use `push-to-pull-request-branch` once.
6. After requesting the push, use `add-comment` with the body exactly
   `@coderabbitai review` so CodeRabbit performs the next incremental review even
   when the PR author is ignored for automatic reviews.

If no active actionable findings remain, call `noop` with a concise reason. Do
not make speculative changes, request another review, mark the PR ready, approve
it, or merge it.
