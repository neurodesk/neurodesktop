---
title: ASTRA integration
description: The astra/lc command-line tools, the ASTRA agent skill for the
  bundled coding agents, and the read-only provenance viewer
parent: ../architecture.md
status: current
last-reviewed: "2026-07-31"
---

# ASTRA integration

Part of [Architecture](../architecture.md). The assessment and design record
behind this integration is
[ASTRA and Lightcone integration](../designs/astra-lightcone-integration.md);
focused tests are listed in
[Testing](../testing.md#focused-tests-by-area).

## ASTRA and Lightcone command-line tools

`astra` comes from the single `astra-tools` install in the conda environment —
the same one the viewer imports — so the CLI and the schema the viewer
validates against can never drift apart. A second isolated copy is deliberately
not installed; the build asserts that exactly `/opt/conda/bin/astra` answers on
`PATH`.

`lc` (`lightcone-cli`) is installed as an isolated `uv` tool under
`/opt/uv/tools/lightcone-cli` and linked onto `PATH`, so its Dask and Snakemake
dependency graph cannot perturb JupyterLab. `uv` itself is on `PATH` for that
reason; ordinary `uv tool` operations stay user-local at runtime.

## ASTRA agent skill

A commit-pinned checkout of the Lightcone Research agent marketplace is stored
at `/opt/neurodesktop/agent-skills`. All three bundled coding agents get the
same ASTRA skill from it, without a first-run marketplace download:

| Agent | Mechanism | Hooks |
| --- | --- | --- |
| Codex | `codex plugin add astra@lightcone-research` | yes |
| Claude Code | `claude plugin install astra@lightcone-research` | yes |
| OpenCode | `SKILL.md` copied to `~/.config/opencode/skills/astra` | no |

OpenCode has no marketplace client, but it discovers Claude-format skills from
`~/.config/opencode/skills`, `~/.claude/skills`, and `~/.agents/skills`. The
skill is copied out of the same pinned checkout, so all three agents read
identical guidance from one source of truth. The plugin's hooks are a
Claude/Codex mechanism and are not copied — OpenCode gets the skill, not the
on-save validation hook.

Those hooks parse their payloads with `jq`, which the image installs for that
purpose. Without it every hook exits non-zero and silently contributes no
validation context, so `tests/container/test_astra_agent_skills_image.py`
drives the real hook scripts end to end rather than only checking that the
plugin is listed. That test also asserts that the pinned marketplace commit's
`astra-pins.sh` matches the installed `astra-tools` and `astra-spec`: the skill
must teach the schema version that `astra validate` actually speaks.

Only the `astra` plugin is enabled. The marketplace also ships `reproduction`
(`assess-reproducibility`, `reproduce`, `figure-comparison`), which is
deliberately left out because its workflows drive long autonomous replication
loops that should not be on by default in a shared scientific image. Users can
add it themselves from the same local marketplace with no network access.

## ASTRA provenance viewer

The image installs the in-repo `extensions/astra-viewer` wheel as
`neurodesk_astra_view`. Its public seam is deliberately small:

```python
from neurodesk_astra_view import AstraView, build_graph

AstraView(
    "astra.yaml",
    universe="universes/bet-f-0-5.yaml",
    run="run-manifest.json",        # optional
    mode="flow",                    # flow, decisions, or evidence
)
```

`adapter.py` is the only viewer module coupled to `astra-spec==0.0.12`. It runs
the public schema and semantic validators over raw YAML, resolves external
analysis and child-universe references recursively, and checks every resolved
real path against the ASTRA project root before it is opened. A spec that
declares a different ASTRA version renders with a warning banner while being
validated against the installed release — the same stance `astra validate`
takes — and the warning survives into the error view when that validation
fails; only an unsupported installed `astra-spec` package hard-fails. The rest
of the package consumes qualified, schema-independent entity records.
`build_graph()` is pure and JSON-serializable; the anywidget is only a renderer
over that result.

The frontend concatenates the checked-in Cytoscape.js 3.34.0 distribution with
the widget renderer at import time. It has no npm build, CDN import, fetch, or
other runtime network path. Flow, Decisions, and Evidence modes filter the same
Cytoscape instance, preserving positions and selection. Prior Insights,
findings, and their Evidence sources remain distinct nodes and only
schema-authoritative links are drawn.

Without a run, the graph is grey `spec-only`. Lightcone manifests, `lc status`
output, and Workflow Run RO-Crates are amber unless passing verification is
explicit. An explicit declared-container plus `runtime: none` mismatch is red
and non-dismissible. Every recorded artifact is rehashed before it is trusted,
so a stale hash or size fails the whole graph closed rather than rendering a
partial one. Previews stay under the directory containing `astra.yaml`.

`tests/fixtures/astra-bet` is the source for the shipped worked example,
installed read-only at `/opt/neurodesktop/examples/astra-bet`. It is
specification-only, so it renders grey; users copy it out as a starting point
for their own analysis.

## File-browser viewer

Double-clicking an `astra.yaml` (or `*.astra.yaml`) in the JupyterLab file
browser renders the same viewer without a kernel, the way NIfTI volumes open
in NiiVue. The `neurodesk_astra_view.serverext` Jupyter server extension
answers `GET /neurodesk-astra-view/graph?spec=…[&universe=…][&run=…]` by
running `build_graph()` server-side — request paths are workspace-relative and
rejected with a 404 before any read when absolute, traversing, or resolving
outside the server root — and serves the anywidget's own frontend at
`/neurodesk-astra-view/asset/(esm|css)`, so the file-browser viewer and the
notebook widget are one frontend with no second copy to drift. The
`neurodesk-launcher:astra-viewer` plugin registers the pattern file type and a
read-only default widget factory (`ASTRA Viewer`) over those endpoints, with a
universe picker fed by the spec's sibling `universes/` directory. Because the
factory is the pattern file type's default, agent-authored chat links to an
`astra.yaml` open in the viewer too, and a disabled plugin degrades to the
text editor. Editing stays on `Open With > Editor`; a save from that shared
context re-renders the graph.
