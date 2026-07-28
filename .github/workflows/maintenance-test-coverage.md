---
name: Daily Maintenance - Test Coverage
description: Add focused tests at important unprotected behavioral boundaries.
labels: [automation, maintenance, tests]
on:
  schedule: daily
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
timeout-minutes: 30

imports:
  - uses: .github/workflows/shared/maintenance-base.md
    with:
      category: test-coverage
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
