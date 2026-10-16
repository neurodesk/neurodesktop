---
title: ASTRA integration
description: The astra/lc command-line tools, Lightcone agent skills and hooks
  for the bundled coding agents, and the read-only provenance viewer
parent: ../architecture.md
status: current
last-reviewed: "2026-09-21"
---

# ASTRA integration

Part of [Architecture](../architecture.md). The assessment and design record
behind this integration is
[ASTRA and Lightcone integration](../designs/astra-lightcone-integration.md);
focused tests are listed in
[Testing](../testing.md#focused-tests-by-area).

## ASTRA and Lightcone command-line tools

The [shared-environment investigation](../designs/image-packaging-and-lightcone.md)
records the dependency comparison and validation needed to retire Lightcone's
isolated environment.

`astra` on the image `PATH` comes from the `astra-tools` install in the conda
environment — the same one the viewer imports. The isolated Lightcone tool
environment receives the exact same ASTRA pin, but does not expose a second
`astra` entry point on the image `PATH`, so its schema cannot drift from the
viewer. The build asserts that exactly `/opt/conda/bin/astra` answers there.

`lc` (`lightcone-cli`) is installed as an isolated `uv` tool under
`/opt/uv/tools/lightcone-cli` and linked onto `PATH`, so its Dask
dependency graph cannot perturb JupyterLab. `uv` itself is on `PATH` for that
reason; ordinary `uv tool` operations stay user-local at runtime. The image
build selects `/opt/conda/bin/python` explicitly and disables uv-managed Python
downloads, so changes to uv's default Python release cannot change the
Lightcone interpreter.

Lightcone 0.5.0 uses native compute allocation and materialization. The image
verifies its source archive and pins ASTRA to the viewer's versions without
changing upstream dependency declarations. It carries no isolated Snakemake
or packaging override. A separate source-anchored adapter enables only the
explicit external-module profile described below.

## Recording what ran

`astra validate` checks a specification against the schema. It never compares
a recipe's `container:` with anything that executed, and it does not require
one at all, so a spec whose recipes shell out to pinned neuroimaging tools
while naming no software validates clean. That leaves the declaration a typed
assertion: an agent can write the wrong version and every validation still
passes.

`/usr/local/bin/neurodesk-astra-provenance` closes that from the execution
side, and [the analysis contract](../../config/agents/AGENTS.md) requires both
halves. Its `publish` subcommand performs the atomic temporary-to-final rename
the contract already mandates, and writes a `<output>.prov.json` sidecar
first, so a published artifact never exists without its provenance record. The
sidecar holds the resolved binary path, the version that binary reported for
itself, the loaded modules from `LOADEDMODULES`, the container image read out
of the transparent-singularity wrapper or the `_LMFILES_` modulefiles, the
producing script's digest, the hostname, and the Slurm job ID. It refuses to
publish a tool it could not get a version out of rather than record a claim it
did not observe.

Its `check` subcommand walks the specification, resolves each output's
effective `container:` through the analysis tree, and fails when a recipe with
a `command` names no software, when nothing recorded its run, or when the
declared string carries no version token the tool actually reported. Version
tokens are compared whole, so `2.5.1` does not satisfy a declared `12.5.10`.
That is the completion check agents run, and it needs no judgment.

The two paths are mutually exclusive by design: the optional `lc` path below
refuses a declared `container:` for the same reason the viewer paints a
declared-image-plus-`runtime: none` manifest red.

## Executing an analysis with `lc`

The default Neurodesktop flow is one plain `sbatch` script per analysis step
with `module load` pinning each tool. It produces no manifest, so the viewer
reads `spec-only` — see [the trust ladder](#astra-provenance-viewer) — and that
is the honest reading, not a defect.

`neurodesk-lc-materialize` is the optional Lightcone 0.5 module path. Run it
from the project root, outside an existing allocation. It uses native
`lc compute launch`, waits for readiness, runs `lc materialize`, and releases
only the full cluster ID allocated by this invocation. It does not retry an
ambiguous submission. After an interrupted submission, inspect `lc compute
status` before submitting again.

Prepare the repository explicitly with `git init` and `lc init`. Configure your
Git identity, supply the scientific inputs and scripts, and commit the project.
Each executable output needs `format`, such as `nii.gz`. Put walltime on the
allocation, not `recipe.resources.time_limit`. Declare a license appropriate to
your project in `[project].license` to enable Lightcone's maintained RO-Crate.
The helper refuses a missing license, dirty project, uncommitted module profile,
or competing `status.json`, `manifest.json`, or `run-manifest.json` evidence.
It never commits author edits or chooses a license or author for you.

```toml
[tool.neurodesk.lightcone]
execution = "modules"
modules = ["fsl/6.0.7.22"]
```

```bash
neurodesk-lc-materialize --cpus 2 --memory 8 --time 2h baseline/bet_brain
```

The installed `/opt/neurodesktop/lightcone-compute.yaml` offers one native Slurm
node with two CPUs, 8 GiB, partition `neurodesktop`, and the isolated Lightcone
Python. It disables local fallback. This is a requested resource shape, not a
capacity probe. A smaller container or host cluster needs a suitable user
`~/.lightcone/compute.yaml` or explicit `LC_COMPUTE_CONFIG`. Inspect the catalog
with `lc compute resources --json` and `lc compute launch --dry-run --json`.
Driver and workers need the same project, interpreter, and connection root.

The adapter reloads the committed exact module pins on each worker. It includes
the ordered pins in `env_version`, so a pin change reports `behind` and
`--refresh` remakes the output. Module recipes intentionally use the upstream
open backend. The manifest records `mechanism: none`, `fs: open`, and the
module pins. Per-task HOME, scratch, and GPU masks remain in effect, and
inherited Bash initialization cannot override the loaded environment.
This is neither filesystem isolation nor cryptographic verification of CVMFS
packages. Unprofiled projects retain upstream sandbox behavior. Module profiles
cannot also declare `[tool.lightcone.image]`; ASTRA `container:` is a software
label and does not select a runtime image.

For ordinary uv projects, use `lc compute launch`, `lc run CLUSTER -- COMMAND`,
`lc materialize CLUSTER TARGET`, and `lc compute down CLUSTER` directly.
`lc materialize --check TARGET` is the freshness gate. `lc status --json` is a
read-only report whose successful exit does not mean outputs are current.

The viewer reads `ro-crate-metadata.json` through structured action, result, and
manifest relationships. Canonical manifests establish historical execution.
Status snapshots are supported explicitly and retain `current`, `behind`, and
`stale` distinctions, but a snapshot without a matching canonical manifest does
not establish execution. Unmatched universes and never-materialized records
stay grey. Preview files must remain inside the project and match their recorded
hashes, including annex symlinks. Missing annex content means no preview.
No 0.5 status, manifest, crate, or sandbox attestation supplies passing output
verification, so this path reaches amber and never synthesizes green.

## Lightcone agent skills

A commit-pinned checkout of the Lightcone Research agent marketplace is stored
at `/opt/neurodesktop/agent-skills`. The self-contained `reproduction` plugin
provides `astra`, `assess-reproducibility`, `reproduce`, and
`figure-comparison`; all three bundled coding agents get the same four skills
without a first-run marketplace download:

The checkout stays at the last reviewed commit that contains that plugin.
The retained plugin teaches ASTRA specifications and reproduction, not the
Lightcone execution lifecycle. Its skills and hooks at this exact commit contain
no obsolete `lc run` flags or Lightcone manifest parsing. The image analysis
contract supplies the native 0.5 execution guidance. During the image build, exact
anchors update the retained plugin's `astra-pins.sh` from its historical
versions to the image's current ASTRA Tools and ASTRA Spec pins. This preserves
the stable reproduction workflow without letting its validator schema drift.

| Agent | Mechanism | Hooks |
| --- | --- | --- |
| Codex | `codex plugin add reproduction@lightcone-research` | yes |
| Claude Code | `claude plugin install reproduction@lightcone-research` | yes |
| OpenCode | skills copied to `~/.config/opencode/skills`; local adapter in `~/.config/opencode/plugins` | yes |

The reproduction plugin already bundles ASTRA, so the standalone `astra`
plugin is not installed alongside it. That avoids registering the same skill
and hooks twice.

OpenCode does not consume the marketplace's Claude/Codex hook manifest. The
image therefore installs
[`opencode_lightcone_hooks.js`](../../config/agents/opencode_lightcone_hooks.js)
as a native OpenCode plugin. It executes the hook scripts from the same pinned
`reproduction` checkout: `SessionStart` context is added through OpenCode's
system transform, while the read reminder and write/edit validation results
are appended to the corresponding tool output so the model sees them. Hook
failures stay non-blocking, matching the upstream informational hooks.

Those hooks parse their payloads with `jq`, which the image installs for that
purpose. Without it every hook exits non-zero and silently contributes no
validation context, so `tests/container/test_astra_agent_skills_image.py`
drives the real hook scripts end to end rather than only checking that the
plugin is listed. That test also asserts that the pinned marketplace commit's
`astra-pins.sh` matches the installed `astra-tools` and `astra-spec`: the skill
must teach the schema version that `astra validate` actually speaks.

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

`adapter.py` is the only viewer module coupled to `astra-spec==0.0.14`. It runs
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
in FreeBrowse. The `neurodesk_astra_view.serverext` Jupyter server extension
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
`manifest.json`, `status.json`, `ro-crate-metadata.json`. Document tests open
each supported filename and check the rendered evidence. None present sends
no `run=`, which is the honest
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
