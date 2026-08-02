---
title: ASTRA integration
description: The astra/lc command-line tools, the ASTRA agent skill for the
  bundled coding agents, and the read-only provenance viewer
parent: ../architecture.md
status: current
last-reviewed: "2026-08-01"
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

## Executing an analysis with `lc`

The default Neurodesktop flow is one plain `sbatch` script per analysis step
with `module load` pinning each tool. It produces no manifest, so the viewer
reads `spec-only` — see [the trust ladder](#astra-provenance-viewer) — and that
is the honest reading, not a defect.

`/opt/neurodesktop/astra_lc_run.sbatch` is the optional second path, for a
project that wants the badge to reflect a real run. `lc run` never submits to
Slurm itself: it always dispatches through Dask, and when `SLURM_JOB_ID` is set
it starts an in-process scheduler and launches one `dask worker` per allocated
node with `srun`. So `lc` goes *inside* an allocation rather than in front of
one, which is all the template is. Released `lightcone-cli` 0.4.0 has no
`--async` or `sbatch` submission of its own.

The template exports `/opt/uv/tools/lightcone-cli/bin` onto `PATH` because `lc`
shells out to the `dask` CLI for those workers and neither is on the default
`PATH`; that omission is the most likely way the path breaks. It writes
`lc status --json` to `status.json` beside `astra.yaml`, renaming it into place
so a half-written manifest never becomes evidence, and refuses to write at all
when another recognised manifest is already there, since two beside one spec
fail closed and blank the graph.

Two things it deliberately refuses. A spec that declares a `container:` is
rejected: Apptainer is not one of `lc`'s runtimes (`docker`, `podman`,
`podman-hpc`, Kubernetes), so the declared image would be recorded as used
without ever running — the red `provenance-mismatch` the viewer exists to
expose. And a `module load` without an explicit version is refused, matching
the standing agent rule.

This path tops out at amber `executed-unverified` and cannot reach green.
`lc verify` prints its result to the console and stamps nothing into the
manifest, and Neurodesktop does not synthesize a verification record it did not
earn. The [integration record](../designs/astra-lightcone-integration.md)
covers why execution was left out of the first pass and what upstream work
would raise the ceiling.

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

That stance extends to spellings the schema has retired. A spec written before
`astra-spec` RFC-0002 (which replaced `authors` and the sectioned `narrative`
map with one free-prose `description`), or before `astra-tools` scoped
`Option.insights` to the declaring analysis, is read rather than refused: the
narrative sections are folded into `description`, an undefined top-level key is
ignored, and a bare option insight naming an ancestor's insight is read as the
`../` reference it used to mean. Each adoption is reported as a warning, so the
graph never silently disagrees with the file. The limits are deliberate —
adoption only ever applies to top-level keys and to references with no other
possible target, because a stray key inside a decision or output is far likelier
to be an authoring mistake than schema drift, and it stays an error. Where a
retired spelling has been adopted, semantic validation runs on the resolved tree
instead of the raw file, since the released validator would otherwise re-read
the retired spelling straight off disk.

The drawn picture is a *presentation projection* of that semantic graph,
derived by `projection.py` so a complex workflow stays readable: one `stage`
node stands in for each analysis (inputs flow into the stage, the stage
produces its outputs, so input×output cross products collapse into paths);
`from`-aliased inputs fold into their canonical source so a re-exported
record draws once, inputs are grouped by id family and outputs by (type,
recipe family, decision contract) once they would crowd a row, with a
compaction pass that bounds the drawn output nodes; each stage's decisions
fold into one collapsed, click-to-expand `decision-cluster` whose members
carry their selected values;
evidence folds into the finding or insight it backs; and a synthetic `result`
node anchors the bottom, concluded by the findings. Every projection node
carries `members` — the semantic node ids it stands for — and the inspector
resolves those for run facts, recipes, artifacts, and previews.

The frontend is a self-contained SVG renderer (`static/index.js`): no
vendored graph library, no npm build, CDN import, fetch, or other runtime
network path. Flow draws the dataflow skeleton, Decisions adds the decision
clusters, and Evidence swaps to the claims subgraph — each mode is a strict
filter, and the renderer rebuilds the drawing on every mode switch, cluster
expansion, and selection, so each filter re-lays out exactly what it draws.

Node placement comes from `layout.py`, which ranks a graph by longest path so
a producer always outranks its consumers, pushes a node nothing feeds down to
sit above the earliest thing it does feed, and orders each rank by barycenter
to reduce crossings. `projection.py` runs it once per view — a `rank`/`order`
pair for the dataflow-centred modes and an `evidence_rank`/`evidence_order`
pair for the Evidence subgraph — and the renderer only turns the active pair
into coordinates, compacting rows over the visible nodes and placing decision
clusters beside their stage. No layout arithmetic lives in the frontend.

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
answers `GET <Jupyter Server base URL>/neurodesk-astra-view/graph?spec=…[&universe=…][&run=…]`
by running `build_graph()` server-side — request paths are workspace-relative
and rejected with a 404 before any read when absolute, traversing, or resolving
outside the server root — and serves the anywidget's own frontend, also under
the Jupyter Server base URL, at
`/neurodesk-astra-view/asset/(esm|css)`, so the file-browser viewer and the
notebook widget are one frontend with no second copy to drift. The
`neurodesk-launcher:astra-viewer` plugin registers the pattern file type and a
read-only default widget factory (`ASTRA Viewer`) over those endpoints, with a
universe picker fed by the spec's sibling `universes/` directory. Because the
factory is the pattern file type's default, agent-authored chat links to an
`astra.yaml` open in the viewer too, and a disabled plugin degrades to the
text editor. Editing stays on `Open With > Editor`; a save from that shared
context re-renders the graph.

The notebook widget is handed its run evidence as `AstraView(spec, run=…)`; a
double-click has nobody to hand it one, so the plugin discovers it from the
spec's own directory and fills in `run=` itself. It looks for exactly the
filenames `manifest._directory_run_file` accepts — `run-manifest.json`,
`manifest.json`, `status.json`, `ro-crate-metadata.json` — and a unit test
holds the two lists together, since a name only the frontend knows is a
manifest that never loads. None present sends no `run=`, which is the honest
`spec-only` reading of a directory with no evidence in it; two or more sends
the *directory*, so the one ambiguity rule in `manifest.py` refuses it rather
than the frontend silently picking a file. A `Refresh` beside the universe
picker re-runs that discovery: run evidence appears when a job finishes,
which is not a change to the spec and so never raises `fileChanged`. A refresh
keeps the universe the reader had selected.

The default plain-`sbatch` flow emits no manifest, so an analysis run that way
reads `spec-only` by design. Discovery is what lets evidence produced by
[the optional `lc` path](#executing-an-analysis-with-lc) — or by any other
producer of a recognised manifest — be read at all.
