---
name: Issue Investigator
description: Investigate new issues and propose focused fix pull requests when needed.
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
  model: ${{ vars.GH_AW_MODEL_AGENT_CODEX || vars.GH_AW_DEFAULT_MODEL_CODEX || 'kimi-k2.7' }}
  env:
    OPENAI_BASE_URL: "https://llm.neurodesk.org/openai"
    OPENAI_API_KEY: ${{ secrets.CODEX_API_KEY || secrets.OPENAI_API_KEY }}

strict: true
max-turn-cache-misses: 50
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
  dispatch-workflow:
    workflows:
      - build-neurodesktop
      - build-neurodesktop-test
      - build-neurodesktop-dev
      - test-cvmfs
      - test-objectstorage
      - jupyter_test_main
      - "notebook_(FSL_bet)_workflow"
      - self-hosted-runner-test
    max: 1
  noop:
    report-as-issue: false
---

# Issue Investigator

## Task

Investigate the issue for this run and decide whether the repository needs a code, workflow-support, documentation, or test fix.

For an `issues` event, use `${{ github.event.issue.number }}` as the issue number. For a `workflow_dispatch` run, use `${{ github.event.inputs.issue-number }}` and treat `${{ github.event.inputs.retry-reason }}` as prior context.

Use `gh` through the GitHub tool to read the issue, comments, linked pull requests, related checks, and relevant repository files. Pull only the context needed for the reported symptom. Reproduce the issue locally when practical, then run the smallest focused validation that gives useful evidence.

## Decision Rules

- If the issue describes an actionable bug or maintenance problem that can be fixed within the allowed files, make the smallest coherent change, add or update focused tests when appropriate, run relevant validation, and use `create-pull-request`.
- If the issue is unclear, duplicate, out of scope, or needs a human product or infrastructure decision, use `add-comment` with the evidence, the current blocker, and the next concrete question or owner action.
- If the evidence shows the issue was probably a transient CI, registry, network, or service failure and no repository change is needed, use `dispatch-workflow` to rerun the most relevant allow-listed workflow once, then use `add-comment` explaining what was transient and what was rerun.
- Do not dispatch recursively or rerun unrelated workflows. If this run was started by `workflow_dispatch`, dispatch another workflow only when the new evidence still points to a transient failure that an allow-listed workflow can verify.
- If no visible repository or issue action is needed, call `noop` with a concise reason.

## Pull Request Expectations

Keep pull requests narrow and reviewable. Include the issue number in the title or body, summarize the root cause, list the validation run, and avoid unrelated refactors. If the best fix would require files outside the allowed pull request scope, do not work around the guardrail; comment with the exact recommended follow-up instead.
