---
import-schema:
  category:
    type: string
    required: true

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
  create-pull-request:
    title-prefix: "[maintenance] "
    branch-prefix: "agentic/maintenance-${{ github.aw.import-inputs.category }}-"
    labels: [agentic-workflow]
    draft: true
    protected-files: request_review
    max-patch-files: 20
    allowed-files:
      - "Dockerfile"
      - ".codespellrc"
      - ".dockerignore"
      - ".gitignore"
      - ".trivyignore.yaml"
      - "AGENTS.md"
      - "CLAUDE.md"
      - "README.md"
      - "analyze_image_size.sh"
      - "build_and_run.bat"
      - "build_and_run.sh"
      - "neurodesk.yml"
      - "stop_and_clean.bat"
      - "stop_and_clean.sh"
      - ".github/actions/**"
      - ".github/containerscan/**"
      - "config/**"
      - "docs/**"
      - "extensions/**"
      - "scripts/**"
      - "tests/**"
  noop:
    report-as-issue: false
---

# Shared Weekly Maintenance Contract

Work only on maintenance category `${{ github.aw.import-inputs.category }}`.

## Before Editing

1. Read `AGENTS.md` and the relevant sections of `docs/testing.md`,
   `docs/architecture.md`, and `docs/environment-variables.md` before changing
   behavior they govern.
2. Search open pull requests for an `agentic-workflow` PR whose title starts
   with `[maintenance] ${{ github.aw.import-inputs.category }}:`. If one exists,
   call `noop`; do not create a competing or follow-up pull request.
3. Inspect only enough code, tests, history, and recent Actions evidence to
   prove one candidate. Use at most 12 read commands and at most 2 external
   version or service probes before choosing an output.

## Change Contract

- Make at most one coherent maintenance change per run. Prefer a small change
  with strong evidence over a broad cleanup.
- Stay inside the named category. Do not edit agentic workflow sources,
  generated workflow lock files, or unrelated code.
- Preserve public behavior unless this category explicitly requires a version
  update. Do not weaken assertions, hide failures, add blanket retries, or
  silence diagnostics to make validation pass.
- Follow `docs/testing.md`. Add or update focused tests when behavior changes,
  and run the smallest relevant test set plus any mandated container or
  workflow validation. If required validation cannot be completed, revert the
  candidate and call `noop`.
- Review the final diff for accidental generated files, caches, lockfile drift,
  secrets, and unrelated formatting before requesting a pull request.

## Output Contract

When a validated change exists, create one draft pull request titled
`[maintenance] ${{ github.aw.import-inputs.category }}: <concise summary>`.
The body must explain the evidence, why the change is safe, and the exact
validation run. CodeRabbit will review the draft and a separate workflow will
apply validated findings. Never mark the pull request ready or merge it.

If there is no proven, safely testable improvement, call `noop` with a concise
reason. Never create a cosmetic or empty pull request merely because the
workflow ran.
