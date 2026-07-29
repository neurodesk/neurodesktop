---
name: Weekly Maintenance - Flaky Tests
description: Stabilize one recurring test failure by fixing its proven root cause.
labels: [automation, maintenance, tests, ci]
on:
  schedule: weekly
  workflow_dispatch:

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
  - uses: .github/workflows/shared/maintenance-base.md
    with:
      category: flaky-tests
---

# Flaky Tests

Inspect a bounded sample of recent Actions runs and fix one recurring,
reproducible test flake at its root cause.

Inspect at most the 20 most recent relevant runs and at most 2 representative
failed job logs. Require the same failure signature in at least two independent
runs, then reproduce it locally or construct a deterministic test that exposes
the race, leaked state, ordering dependency, clock dependency, or environment
assumption.

Fix the underlying nondeterminism. Do not skip or quarantine the test, weaken
its assertions, add an unconditional retry, add arbitrary sleeps, or merely
increase a timeout. If the evidence points to external infrastructure or a
one-off failure rather than repository behavior, call `noop`.
