---
name: Weekly Maintenance - Updates
description: Apply one verified low-risk dependency, action, tool, or base-image update.
labels: [automation, maintenance, dependencies]
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
      category: updates

safe-outputs:
  report-failure-as-issue: false
---

# Updates

Inventory pinned dependencies, base images, installed tools, composite GitHub
Actions, and package manifests, then apply one current, compatible, low-risk
update. Workflow YAML and generated workflow locks are outside this automated
workflow's write scope.

Verify the candidate against the upstream project's official release notes and
the repository's runtime contract. For `jupyter-server-documents`, `ipyniivue`,
`ipywidgets`, `jupyterlab_widgets`, or `ipykernel`, first read the retirement
table in `docs/architecture/jupyter-ai.md` ("Retiring the anchored
workarounds"): a build-time patch anchor that fails after the bump means the
seam must be removed rather than re-anchored, unless the release notes show
the bug is still open, and a seam that still applies after upstream fixed the
bug elsewhere must be removed too. Prefer security fixes and patch/minor
updates with a clear compatibility story. Never replace a pin with a floating
tag, edit generated lock data by hand, or combine unrelated upgrades. Generate
package lock changes with the owning package manager.

Run the focused tests for the affected component and every validation required
by `docs/testing.md`. For a build-time or runtime-image update, do not create a
pull request unless the required container validation succeeds.
