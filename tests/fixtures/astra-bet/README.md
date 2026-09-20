# BET threshold sensitivity — worked ASTRA example

A minimal but complete `astra.yaml` you can copy as the starting point for your
own analysis. Its repository source doubles as a regression fixture. It records
one methodological decision — FSL BET's fractional
intensity threshold — with two defensible options, and connects a prior
insight, four outputs, and an artifact-backed finding to that decision.

Neurodesktop installs it at `/opt/neurodesktop/examples/astra-bet`. Copy it
somewhere writable before editing:

```bash
cp -r /opt/neurodesktop/examples/astra-bet ~/my-analysis
cd ~/my-analysis
astra validate astra.yaml
```

Double-click `astra.yaml` in the JupyterLab file browser to open it in the
ASTRA viewer, or render it from a notebook:

```python
from neurodesk_astra_view import AstraView

AstraView("astra.yaml", universe="universes/bet-f-0-5.yaml")
```

The example is specification-only: it declares what an analysis would do and
what its decision means, and nothing in it has been executed. The viewer says
so with a grey `spec-only` badge. Supply a Lightcone run manifest, `lc status`
output, or Workflow Run RO-Crate as the viewer's `run` argument to overlay
real execution evidence.

Two references are deliberately left for you to supply, so the example
validates and renders without shipping data or an FSL environment:

- `inputs` points at an OpenNeuro `ds000114` T1w image that is not shipped
  here; replace it with a path to your own data.
- each `recipe.command` calls `src/materialize-bet-output.sh`, a script this
  example does not ship. Write it (or replace the commands outright) before
  you execute anything; `astra validate` checks the specification, not the
  existence of the commands it names.

Before running your copy, remove the illustrative `findings:` entry. It
demonstrates the schema and is not an observation from your data. Add findings
after inspecting the artifacts from your run. Preserve observations when
extending an actual prior analysis.

## Script interfaces and small outputs

Draft the recipes before execution and implement their positional interfaces
under `src/`, using names such as `analysis_01_bet.sh`. Update the example's
recipe commands to match. A dispatch argument can select the artifact without
requiring a separate script for each output:

```bash
bash src/analysis_02_qc.sh <artifact_id> <input> <threshold> <output>
```

Each output's recipe must name the invocation that produces it. When several
invocations are shorter than scheduling latency and use the same resources,
run those resolved recipe commands together in one Slurm allocation. Use an
array when independent scheduling, retries, or useful parallelism justify it.
Have a validation step that already computes mask volume write the metric
artifact rather than adding a job to compute it again.

Create `logs/` from the project root before submitting jobs. Match requests to
the selected partition and follow `/opt/AGENTS.md` for dependency submission,
pending-job diagnosis, and completion checks.
