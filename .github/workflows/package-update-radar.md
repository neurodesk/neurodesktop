---
name: Weekly Package Update Radar
description: Survey every pinned package, tool, action, and base image, then report the available upgrades in one tracking issue.
labels: [automation, maintenance, dependencies]
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
timeout-minutes: 40

imports:
  - uses: .github/workflows/shared/agentic-models.md

network:
  allowed:
    - defaults
    - github
    - python
    - node
    - containers
    - linux-distros
    - llm.neurodesk.org

tools:
  github:
    mode: gh-proxy
    toolsets: [default]

safe-outputs:
  threat-detection:
    engine: false
  create-issue:
    title-prefix: "[package-updates] "
    labels: [agentic-workflow, dependencies]
    max: 1
  add-comment:
    max: 1
    target: "*"
    issues: true
    pull-requests: false
    discussions: false
    hide-older-comments: true
  noop:
    report-as-issue: false
---

# Package Update Radar

Report which pinned packages have newer upstream releases. This workflow is a
read-only survey: it never edits repository files and never opens a pull
request. `maintenance-updates` consumes this radar and applies at most one
verified update per week, so the radar's job is to keep an accurate, ranked
candidate list rather than to change anything.

## Inventory Surfaces

Cover every surface that pins a third-party version:

- `Dockerfile` build arguments, including the base image tag, Apptainer, Go,
  gRPC, CVMFS, Guacamole, Tomcat and the Jakarta EE migration tool,
  code-server, OpenCode, and any other `ARG *_VERSION` pin.
- `Dockerfile` package installs: `pip install` pins, `npm pack`/`npm install`
  pins, conda pins, and version-constrained apt packages.
- JupyterLab extension and Python pins that `docs/architecture.md` and
  `docs/testing.md` describe as version-sensitive, such as Notebook
  Intelligence, `jupyterlab_myst`, `jupyterlab-niivue`, and `ipywidgets`.
- `extensions/neurodesk-launcher/package.json` and
  `extensions/neurodesk-launcher/pyproject.toml` dependency ranges.
- Composite actions under `.github/actions/**` and the pinned action refs used
  by repository workflows.
- Pinned tool versions in `scripts/**` and `config/**`.

Ignore vendored `node_modules` trees; report the declared dependency instead of
its transitive lockfile entries.

## Evidence Budget

- Build the inventory from repository files first. Use at most 15 read commands
  for the whole inventory pass.
- Use at most 20 upstream version probes per run, one per tracked component.
  Prefer a single authoritative source per component: the GitHub releases API,
  the PyPI or npm registry JSON, the distribution package index, or the
  container registry tag list.
- Never probe the same component twice. If a probe fails or is ambiguous,
  record the component as `unknown` with the reason and move on.
- If the inventory is larger than the probe budget, probe the components that
  the previous radar issue ranked highest, note the surfaces you skipped, and
  say explicitly that coverage was truncated.

## Ranking Rules

Classify each component with a newer release as one of:

- **Security** — the gap includes a published advisory or CVE fix. Always rank
  these first and cite the advisory identifier.
- **Ready** — patch or minor gap with published release notes and no breaking
  change relevant to this image.
- **Needs review** — major gap, a breaking change in the release notes, a pin
  that `AGENTS.md` or `docs/testing.md` couples to mandated container
  validation, or a component whose upgrade touches the frontend rebuild of
  Notebook Intelligence or MyST.
- **Blocked** — a newer release exists but something in this repository
  prevents it; state the blocker.

Do not recommend replacing a pin with a floating tag. Do not recommend
hand-editing generated lock data. Treat a component as current when the pin
already matches the newest release for its supported line.

## Report Format

The issue body must contain, in this order:

1. A one-line summary with the run date, the number of components tracked, and
   the counts per classification.
2. A markdown table with the columns `Component`, `Surface`, `Pinned`,
   `Latest`, `Gap`, `Class`, `Evidence`. `Surface` is the file and pin name.
   `Evidence` is the upstream release-notes or advisory URL used.
3. A short `Recommended next update` section naming the single best candidate
   for `maintenance-updates` and why it is the safest high-value pick.
4. A `Coverage` section listing components probed as `unknown`, surfaces
   skipped, and any budget that was hit.

Keep the table to the components that are behind, current-but-security-relevant,
or unknown. Do not pad it with components that are already current.

## Output Contract

Search open issues for one labeled `agentic-workflow` whose title starts with
the `[package-updates]` prefix.

- If no such issue is open, call `create-issue` with the title
  `Pinned dependency radar` and the report as the body. The workflow adds the
  title prefix.
- If one is already open, call `add-comment` on it with the current report.
  Older radar comments are collapsed automatically, so write the comment as a
  complete standalone report rather than a diff against the previous run.

Never open a second radar issue while one is open, and never do both actions in
one run.

Call `noop` with a concise reason when the survey finds no component behind its
latest upstream release and no open radar issue needs correcting.
