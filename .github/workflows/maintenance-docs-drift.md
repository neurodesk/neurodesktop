---
name: Weekly Maintenance - Documentation Drift
description: Reconcile one verified mismatch between documentation and current behavior.
labels: [automation, maintenance, documentation]
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
      category: docs-drift

safe-outputs:
  report-failure-as-issue: false
---

# Documentation Drift

Find one verified mismatch between current repository or runtime behavior and
`README.md`, `AGENTS.md`, `CLAUDE.md`, or `docs/`, then update the stale
documentation.

Prioritize startup flow, supported environment variables, test commands,
component versions, file paths, and operator instructions. Trace the current
implementation and tests before deciding which side is authoritative. This is
a documentation-reconciliation workflow: if the implementation is the broken
side, call `noop` and identify that reason rather than smuggling in a product
fix.

Keep wording precise and update all directly conflicting documentation in the
same PR. Run link, spelling, or focused source-shape checks that are available
for the changed files.
