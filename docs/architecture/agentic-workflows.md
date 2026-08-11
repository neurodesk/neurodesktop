---
title: Agentic CI workflows
description: The agentic issue-investigation workflow and the weekly
  maintenance suite that keep the repository healthy
parent: ../architecture.md
status: current
last-reviewed: "2026-08-10"
---

# Agentic CI workflows

Part of [Architecture](../architecture.md). The workflow catalog, guardrails,
and operating contract are in
[Agentic maintenance workflows](../agentic-maintenance.md).

## Agentic issue investigation

The source workflow in
[`issue-investigator.md`](../../.github/workflows/issue-investigator.md) investigates
new issues and may open a draft pull request labeled `agentic-workflow`. The
generated `issue-investigator.lock.yml` is the executable GitHub Actions
workflow and must be regenerated with `gh aw compile` whenever the Markdown
source changes.

CodeRabbit reviews those pull requests while they are still drafts. Its summary
comment updates trigger the companion
[`issue-investigator-review.md`](../../.github/workflows/issue-investigator-review.md)
workflow. That workflow reads the complete current review, validates all active
findings against the latest PR head, batches valid fixes into one tested commit,
pushes it to the existing PR branch, and explicitly requests the next
incremental CodeRabbit review. The loop stops without changing or merging the PR
when no actionable findings remain; marking the draft ready and merging remain
human decisions.

Every Codex agentic workflow imports
[`agentic-models.md`](../../.github/workflows/shared/agentic-models.md). Its
`neurodesk` model alias lists GLM 5.2 first and Kimi 2.7 second, giving the
workflow firewall an ordered secondary candidate when resolving the model from
the available-model catalog. Each Codex source also installs the pre-agent
timeout detector wrapper: process signals count only on bare harness lifecycle
records, never when the same text appears in repository or tool output. Every
workflow has a hard turn ceiling so prompt-only research budgets cannot grow
without bound. The wrapper lives under `.github/`, which review runs restore
from the base branch before executing pre-agent steps; a pull-request branch
cannot replace this failure-classification code.

## Weekly agentic maintenance

Seven independently scattered weekly workflows inspect test redundancy, missing
coverage, available updates, duplicate abstractions, dead code, documentation
drift, and recurring test flakes. They share the bounded pull-request contract
in
[`maintenance-base.md`](../../.github/workflows/shared/maintenance-base.md): each
category allows one open draft PR, one evidence-backed change per run, and no PR
when the candidate cannot be validated.

All maintenance PRs use the `[maintenance]` title prefix and
`agentic-workflow` label. CodeRabbit reviews them as drafts, then
[`maintenance-review.md`](../../.github/workflows/maintenance-review.md) validates
and batches actionable feedback, pushes once to the existing branch, and asks
CodeRabbit for another incremental review. See
[Agentic maintenance workflows](../agentic-maintenance.md) for the workflow
catalog, guardrails, and operating contract.
