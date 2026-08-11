---
name: Weekly Maintenance - Dead Code
description: Remove one proven unused code path or artifact and its stale references.
labels: [automation, maintenance, cleanup]
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
max-turns: 30
max-turn-cache-misses: 2000
timeout-minutes: 30

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
      category: dead-code
---

# Dead Code

Find and remove one proven unused function, script, configuration branch,
asset, dependency, or compatibility shim together with stale tests and docs
that exist only for it.

Before deleting, check repository references, imports, shell sourcing, Docker
copy/install steps, entry points, generated configuration, runtime string
lookups, Jupyter extension registration, and workflow usage. Treat public APIs,
documented commands, dynamically loaded names, and image/runtime entry points as
used unless there is explicit evidence otherwise. Git history may explain why a
seemingly unused compatibility path still exists.

Run focused tests for adjacent live behavior and any build/config validation
that could detect a missing runtime artifact. Do not remove code solely because
a static scanner cannot see a dynamic caller.
