---
name: Weekly Maintenance - Abstraction Police
description: Consolidate one proven duplicate abstraction while preserving behavior.
labels: [automation, maintenance, architecture]
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
timeout-minutes: 35

imports:
  - uses: .github/workflows/shared/agentic-models.md
  - uses: .github/workflows/shared/maintenance-base.md
    with:
      category: abstraction-police
---

# Abstraction Police

Find one abstraction that has been independently implemented in at least two
places and homogenize it only when the implementations serve the same domain
contract.

Prove semantic duplication by comparing inputs, outputs, error behavior,
ownership, lifecycle, and callers. Similar syntax is not enough. Prefer an
existing canonical implementation when one exists; otherwise put the shared
abstraction with the component that owns the concept. Do not create a generic
helper with flags that merely hides meaningful differences.

Preserve behavior with focused tests for every migrated caller, including its
failure path. Keep the change to one abstraction family and report why the new
ownership boundary is more stable than the duplicate implementations.
