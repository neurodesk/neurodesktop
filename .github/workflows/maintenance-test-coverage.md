---
name: Weekly Maintenance - Test Coverage
description: Add focused tests at important unprotected behavioral boundaries.
labels: [automation, maintenance, tests]
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
      category: test-coverage

safe-outputs:
  report-failure-as-issue: false
---

# Test Coverage

Find one important observable behavior that lacks meaningful automated
protection and add focused coverage for it.

Prioritize startup boundaries, environment-variable behavior, security and
authentication checks, failure handling, generated configuration, and recently
changed production paths. Trace existing tests before choosing a gap; line
coverage alone is not evidence of missing behavioral coverage.

Add a positive case and a realistic failure or boundary case when both are
applicable. Assert public outputs and side effects instead of private function
shape. Prove the test is capable of failing for the targeted regression, and
run the focused test file. Do not combine the test addition with unrelated
production cleanup.
