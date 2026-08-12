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
  - name: Set up Python for focused checkout tests
    uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
    with:
      python-version: "3.12"
  - name: Install focused checkout test dependencies
    run: python -m pip install pytest httpx traitlets
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
      category: test-pruning
---

# Test Pruning

Find one test or tightly related test group that is demonstrably redundant,
obsolete, or coupled only to an implementation detail, then remove or simplify
it without reducing protection of a supported behavior.

Keep discovery concrete and bounded. After the mandatory initial open-PR read,
use one batched inventory command to shortlist tests, then inspect at most 3
candidate modules with at most one repository-read command per candidate.
Prefer small modules with an obviously subsumed assertion, a standalone static
check already exercised by behavioral tests, or coverage for behavior that no
longer exists. Do not start with the largest test files merely because they
contain more tests, and do not branch into another area after selecting a
candidate. By the sixth repository-read command, choose one candidate or call
`noop`; never spend the remaining turns searching for a better candidate.

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

Run the candidate's smallest owning test module immediately before editing and
again after editing. Use `python -m pytest <module> -q` without piping its output;
if a pipeline is unavoidable, enable `set -o pipefail` first. The workflow
preinstalls the checkout-safe `pytest`, `httpx`, and `traitlets` dependencies. If
that Python or pytest invocation is still unavailable, call
`report_incomplete` immediately with the failing command and error; do not probe
other Python installations, search the filesystem, inspect package lists, or
install dependencies ad hoc. Never run the complete checkout or container test
suite. Stop discovery before turn 20 and make the required terminal safe-output
call on or before turn 24.
