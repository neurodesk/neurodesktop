---
name: Weekly Maintenance - Flaky Tests
description: Stabilize one recurring test failure by fixing its proven root cause.
labels: [automation, maintenance, tests, ci]
on:
  workflow_dispatch:

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

sandbox:
  agent:
    id: awf
    model-fallback: false

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
# Keep a hard ceiling above the measured size of productive maintenance runs.
max-turns: 60
max-turn-cache-misses: 2000
timeout-minutes: 60

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
  - uses: .github/workflows/shared/maintenance-base.md
    with:
      category: flaky-tests

safe-outputs:
  report-failure-as-issue: false
---

# Flaky Tests

Inspect a bounded sample of recent Actions runs and fix one recurring,
reproducible test flake at its root cause.

Use one `gh run list` read to inspect at most the 20 most recent relevant runs,
then read at most 2 representative failed job logs. If the initial `gh` read or
either required log read fails, call `report_incomplete` immediately; do not
retry through `gh api`, unauthenticated `curl`, or authentication probes.

Require the same failure signature in at least two independent logs before
running any local test. If no recurring signature is proven within 10 read
commands, call `noop` and stop. Once a signature is proven, reproduce it with
only the smallest owning test module or node, or construct a deterministic test
that exposes the race, leaked state, ordering dependency, clock dependency, or
environment assumption. Never run `pytest tests/unit`, the complete checkout
test suite, or install dependencies ad hoc as a discovery strategy. A failure
caused only by the agent job's incomplete host environment is not a test flake.

Fix the underlying nondeterminism. Do not skip or quarantine the test, weaken
its assertions, add an unconditional retry, add arbitrary sleeps, or merely
increase a timeout. If the evidence points to external infrastructure or a
one-off failure rather than repository behavior, call `noop`.

Call the selected terminal safe-output tool before turn 54. Do not spend the
remaining turns describing another diagnostic step.
