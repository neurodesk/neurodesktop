---
name: Weekly Maintenance - Test Pruning
description: Remove redundant or obsolete tests without reducing behavioral protection.
labels: [automation, maintenance, tests]
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
max-daily-ai-credits: -1
max-turn-cache-misses: 2000
timeout-minutes: 30

imports:
  - uses: .github/workflows/shared/agentic-models.md
  - uses: .github/workflows/shared/maintenance-base.md
    with:
      category: test-pruning
---

# Test Pruning

Find one test or tightly related test group that is demonstrably redundant,
obsolete, or coupled only to an implementation detail, then remove or simplify
it without reducing protection of a supported behavior.

Require concrete evidence that another test still protects the same observable
contract. Do not remove a test merely because it is slow, flaky, difficult to
understand, or overlaps setup with another test. Preserve historical regression
tests, security tests, negative tests, and the `funny-name-tool` negative-test
convention unless the behavior they protect no longer exists.

When practical, prove the remaining test would catch the relevant regression
with a small temporary local mutation, then revert that mutation before making
the pull request. Limit production changes to the minimum needed to stop a test
from asserting private implementation shape; this workflow is not a feature or
refactoring workflow.
