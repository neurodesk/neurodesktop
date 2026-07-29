# Lightcone run-artifact and trust contract

Snapshot: 2026-07-29

Question: Which current Lightcone artifacts may the Neurodesktop ASTRA viewer
treat as authoritative, and what do they justify?

## Decision

For `lightcone-cli==0.4.0`, the per-output
`<output-dir>/.lightcone-manifest.json` is the canonical stored Lightcone run
record. `lc status --json` is a transient, live interpretation of those
manifests against the current spec. `lc verify` is a transient integrity
calculation and emits no JSON or durable verification receipt. Workflow Run
RO-Crate (WRROC) is a derivative publication view whose embedded manifests
remain canonical.

Consequently, a viewer that only reads current stored artifacts can distinguish
`spec-only` from `executed-unverified`, but it **cannot** honestly infer either
`executed-verified` or a local `runtime: none` provenance mismatch. It may show
`executed-verified` only if it performs a fresh, coverage-checked verification
through Lightcone's Python APIs at load time, or consumes a future durable
verification receipt bound to the exact manifests. It may show
`provenance-mismatch` only from positive actual-runtime evidence that
contradicts the declaration; absence of runtime evidence is uncertainty, not a
contradiction.

## Release basis and change check

The current PyPI/GitHub release is still
[`v0.4.0`](https://github.com/LightconeResearch/lightcone-cli/releases/tag/v0.4.0).
The release tag and upstream `main` both resolve to commit
[`8c1e8187f23aaa401bfdcff256ac18e85d943aa3`](https://github.com/LightconeResearch/lightcone-cli/commit/8c1e8187f23aaa401bfdcff256ac18e85d943aa3).
There are therefore no upstream code changes since the 2026-07-27 assessment.
The findings below tighten that assessment by distinguishing declared facts,
actual execution facts, and live computations.

Lightcone calls this manifest schema version 1, but there is no standalone JSON
Schema and `read_manifest()` does not validate required fields or the schema
version. The authoritative schema is the writer implementation and its tests:
the constant is declared in
[`manifest.py`](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/manifest.py#L33-L49),
and the writer constructs the object in
[`write_manifest()`](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/manifest.py#L151-L215).
The viewer adapter must therefore validate `schema_version == 1`, required
fields, types, spec membership, and path confinement itself.

## Artifact hierarchy

### 1. Per-output manifest: canonical stored record

A successful recipe writes one atomic JSON sidecar at
`<output-dir>/.lightcone-manifest.json`. The manifest is written after the
recipe process exits successfully; a failed recipe does not get a manifest
([runner](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/runner.py#L52-L107)).
Its existence is therefore Lightcone's stored claim that one output
materialized successfully. It is not signed and does not authenticate who or
what wrote it.

Schema-v1 fields actually emitted by the current writer are:

| Field | Meaning and trust boundary |
| --- | --- |
| `schema_version` | Literal `1`. Upstream does not reject other or malformed versions on read. |
| `output_id`, `universe_id` | Output and universe identifiers. There is no `analysis_id`; nested-analysis identity must be recovered from the spec/path. |
| `recipe` | Raw authored command template, not the decision/input/output-substituted command and not the runtime wrapper. |
| `decisions` | Scoped resolved decision values used for this output. |
| `code_version` | SHA-256 of raw recipe text, the **resolved image identifier**, and decisions. Runtime kind is intentionally excluded. Referenced script contents are not included merely because the recipe names a script ([hash definition](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/manifest.py#L110-L131)). |
| `container_image` | Raw declared `container:` value from the ASTRA spec, such as `Containerfile`; it is not generally the resolved image/digest and is not evidence that a container ran ([generation config](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/snakefile.py#L420-L458)). |
| `worker_image` | Actual worker image reported through `LIGHTCONE_WORKER_IMAGE`, currently populated for Dask Gateway/Kubernetes workers and `null` elsewhere ([writer](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/manifest.py#L203-L209), [worker environment](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/dask_cluster.py#L268-L325)). |
| `data_version` | SHA-256 of the complete output directory, including relative paths, excluding the manifest and `.snakemake_timestamp`. It is an aggregate directory hash, not a per-file inventory ([hash implementation](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/manifest.py#L52-L80)). |
| `input_versions` | For an upstream Lightcone output, its `data_version`; otherwise an external fingerprint. External files default to `mtime-size`, external directories to SHA-256, and missing paths to `"missing"` ([fingerprinting](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/manifest.py#L93-L107), [writer](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/manifest.py#L175-L193)). |
| `git_sha`, `git_remote` | Current Git `HEAD` and normalized origin URL when available. Dirty-tree state is not recorded; `git_sha` alone does not identify uncommitted recipe/script changes ([Git capture](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/snakefile.py#L123-L165)). |
| `lc_version` | Lightcone version that generated the run configuration. |
| `finished_at` | POSIX completion timestamp. There is no start time or duration. |
| `host`, `slurm_job_id` | Hostname and ambient Slurm job ID. They do not identify the container/runtime. |

The output artifact path is not a field. It is the manifest's parent directory.
The manifest has no list of member files, individual file hashes, MIME types,
sizes, log paths, exit code, start time, resolved command, actual local runtime,
module identity, SIF identity, or verification result.

`.lightcone/snakefile-config.json` does contain a substituted and runtime-wrapped
`shell_command`, but it is regenerated from the current spec, is not copied into
WRROC, and is not bound into the manifest as a stored field. The manifest keeps
only the raw template while `shell_command` lives in the generated config
([generator](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/snakefile.py#L420-L466)).
It is useful diagnostics, not canonical executed-command evidence.

### 2. `lc status --json`: canonical live status, not a run record

`lc status` walks the current spec and manifests and labels each output `ok`,
`stale`, `missing`, or `alias`. `ok` means only that the manifest is present and
the current recomputed `code_version` matches. It does not hash result bytes or
walk the input chain
([status algorithm](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/status.py#L92-L170)).

The JSON shape is unversioned:

```json
{
  "universes": [{
    "universe_id": "baseline",
    "outputs": [{
      "output_id": "main_result",
      "analysis_id": null,
      "status": "ok",
      "recipe_command": "raw {template}"
    }]
  }]
}
```

Those are the only emitted fields
([CLI implementation](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/cli/commands.py#L874-L909)).
There are no paths, hashes, timestamps, tool/schema version, run ID, runtime,
container identity, artifact members, verification outcome, or logs. A saved
status JSON file is an undated snapshot; the viewer should display its statuses
but must not treat it as durable verification evidence.

### 3. `lc verify`: canonical live integrity calculation, no evidence artifact

`lc verify` recomputes each present output directory's `data_version` and checks
recorded upstream Lightcone manifest versions. Its structured Python result has
`output_id`, `universe_id`, `output_dir`, `passed`, `failure`, and `detail`;
failure kinds are `missing_manifest`, `tampered_data`, and `broken_chain`
([verifier](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/verify.py#L32-L138)).

The CLI has no `--json` or output-file option. It prints human-readable text and
returns non-zero when any yielded result fails
([CLI](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/cli/commands.py#L936-L961)).
It also skips an expected output whose directory does not exist. Therefore an
empty, never-run project exits 0 with “All outputs verified.” An exit code or
terminal transcript without an expected-output coverage check is not sufficient
evidence.

The current verifier also does not re-fingerprint external inputs: when a
declared input is not another Lightcone output, it accepts the recorded value
without comparing the current external path
([external-input branch](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/verify.py#L92-L119)).
“Verified” must consequently mean Lightcone output-byte and upstream-manifest
chain consistency, not source-data immutability, authenticity, scientific
correctness, or full reproducibility.

### 4. WRROC: derivative publication view

Lightcone explicitly defines the per-output manifests as canonical and WRROC as
an on-demand publication view
([exporter contract](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/wrroc.py#L1-L33)).
The crate includes `astra.yaml`, universe YAML, each manifest, and optionally
output files. Its JSON-LD maps output directories to `Dataset`, recipes to
`SoftwareApplication`, and each manifest-backed output to a `CreateAction`
([mapping](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/wrroc.py#L13-L33),
[bundle behavior](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/wrroc.py#L90-L125)).

For each manifest-backed action, the exporter sets:

- `endTime` from `finished_at`;
- `actionStatus = CompletedActionStatus` unconditionally;
- `instrument` to the raw recipe, with the declared `container_image` as a
  software requirement;
- `object` to inputs, decisions, `code_version`, and `data_version`;
- `result` to the output dataset.

This mapping is implemented in
[`_add_create_action()`](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/wrroc.py#L439-L531).
`CompletedActionStatus` means a manifest existed during export; it does **not**
mean `lc verify` passed. The crate contains no verification result, runtime
kind, resolved command, logs, start time, or duration. Container entities are
made directly from the declared `container_image`
([container mapping](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/wrroc.py#L618-L689)),
so they are not independent actual-runtime evidence. The JSON-LD action maps
`code_version` and `data_version`, but not `git_sha`, `lc_version`, `host`,
`slurm_job_id`, or `worker_image`; those facts remain available only through the
embedded sidecars. For trust decisions, read the crate's embedded manifests and
regard JSON-LD fields as presentation/index metadata.

## Runtime and log evidence gaps

Current runtime resolution supports Docker, Podman, `podman-hpc`, Kubernetes,
or `none`; `auto` may resolve to `none`
([runtime resolver](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/container.py#L230-L274)).
When runtime is `none`, the container wrapper is a no-op even if the spec
declares an image
([wrapper](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/container.py#L800-L850)).
The selected runtime is not written to the manifest. A manifest with
`container_image: "Containerfile"` and `worker_image: null` can therefore come
from either a successful local Docker/Podman run or uncontained host execution.
Those cases are indistinguishable from the canonical stored record.

Successful recipe stdout/stderr is captured and streamed to the terminal, not
persisted as a run artifact. On a workflow failure, only a bounded Snakemake
stderr tail is written to scratch and its path is printed; no manifest points to
it
([runner streaming](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/engine/runner.py#L74-L107),
[failure tail](https://github.com/LightconeResearch/lightcone-cli/blob/v0.4.0/src/lightcone/cli/commands.py#L696-L766)).
The M4 viewer cannot supply a trustworthy log excerpt from current canonical
artifacts.

## Exact viewer trust rules

Trust is evaluated for the selected spec, universe, and complete set of
recipe-backed outputs. Alias outputs are excluded from verification coverage.
The precedence order is `provenance-mismatch`, `executed-verified`,
`executed-unverified`, then `spec-only`.

| Level | Exact condition for the v0.4.0 adapter |
| --- | --- |
| `provenance-mismatch` | Positive actual-runtime/environment evidence exists and contradicts the declared environment (for example, a future receipt says `runtime: none` while a container was declared, or an actual immutable image identity differs from the resolved declaration). This level must **not** be inferred from `container_image != null && worker_image == null`; that combination is ambiguous today. |
| `executed-verified` | At least one canonical manifest exists; every expected recipe-backed output has a schema-valid matching manifest; live `get_output_status()` returns `ok` for every expected output; a fresh `verify_outputs()` result exists and passes exactly once for every expected output; and any declared execution environment is positively reconciled with actual runtime identity. The badge must state that this proves current Lightcone output-byte/upstream-manifest consistency only. A static viewer that does not perform these read-only computations cannot enter this state with v0.4.0. |
| `executed-unverified` | At least one schema-valid manifest, manifest-backed WRROC `CreateAction`, or saved status `ok`/`stale` indicates a recorded materialization, but any green requirement is absent or failing: verification unavailable/failed, coverage incomplete, status stale/missing, or declared runtime identity unproven. Preserve specific failures (`tampered_data`, `broken_chain`, stale, missing) in details rather than flattening them into success. |
| `spec-only` | No canonical successful-output record exists. This includes no `run` input, status containing only `missing`/`alias`, a workflow-only WRROC with no manifest-backed `CreateAction`, and result directories without valid manifests. Show such directories as an integrity/provenance gap, not as execution evidence. |

For M4, the honest implementation options are:

1. **Verify at load (recommended for the current release):** use the released
   read-only `get_output_status()` and `verify_outputs()` Python APIs, add the
   explicit expected-output coverage check above, and show the verification
   time in the badge details. Do not run a shell command.
2. **Static-only adapter:** ingest manifests/status/WRROC but keep every recorded
   run amber because v0.4.0 has no durable verification receipt.

In both options, a declared local container with no positive actual-runtime
identity remains amber, not green or red.

## Upstream contract required for production execution

Module-mediated execution and first-class Apptainer execution cannot be made
truthful by the v0.4.0 manifest. An upstream schema bump or a manifest-bound
execution receipt needs, at minimum:

- a stable run/invocation ID and per-output status/exit code;
- start/end times and duration;
- raw and fully resolved command (or a content hash plus retrievable command);
- declared environment separately from actual runtime kind;
- immutable actual image/SIF digest and, for Neurodesk modules, module version,
  modulefile/wrapper hash, invoked script hashes, and resolved CVMFS/SIF identity;
- artifact member paths, sizes, hashes, and durable stdout/stderr log paths and
  hashes;
- a verification receipt with schema version, verifier/Lightcone version,
  verification timestamp, selected spec/universe hashes, the complete expected
  output set, per-output result/failure, and hashes of every manifest it covers;
- external-input re-verification or an explicit statement that it was not done.

Until that exists, production claims about module/Apptainer identity must come
from a separate adapter-generated receipt with the same bindings, and the
viewer must label it separately from native Lightcone manifest evidence.

## Disposable CLI checks

A minimal v0.4.0 project was run outside the Neurodesktop checkout with
`container.runtime: none`. The observed manifest contained the 16 fields listed
above. `lc status --json` contained only the documented four per-output fields.
After changing result bytes, status remained `ok` while `lc verify` returned
`tampered_data` and exit 1. On a separate never-run project, status was
`missing`, while `lc verify` returned exit 0 and “All outputs verified.” A WRROC
export contained the manifest and result, set only `endTime` on its
`CreateAction`, and marked the action completed without recording verification.

A second forced run added `container: Containerfile` while keeping explicit
runtime `none`. It completed with no container wrapper, yet the manifest recorded
`container_image: "Containerfile"`, `worker_image: null`, and no runtime field.
This reproduces the ambiguity directly and confirms that current static
artifacts cannot detect the planned `runtime: none` mismatch.

These probes agree with the tagged source and were intentionally disposable;
the source links above are the durable evidence for implementation.
