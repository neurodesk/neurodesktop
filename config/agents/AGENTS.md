# Neurodesk Agent Context

Use this contract for scientific analyses in Neurodesk. Keep the work
reproducible without turning routine discovery, conventional defaults, or short
parameter sweeps into unnecessary user prompts and scheduler jobs.

## Operating contract

1. **Respect choices the user already made.** If the user names FSL BET, do not
   reopen the choice between BET and other extraction tools. A consequential
   parameter still belongs in ASTRA, but the existence of a decision does not
   by itself require a blocking question. For a demonstration with a documented
   conventional default, record and announce that default, then proceed. Ask
   before execution when there is no defensible default, the choice materially
   changes interpretation, safety, runtime, or cost, or the user asks to compare
   alternatives.
2. **Discover before executing.** Confirm a neuroimaging module and its explicit
   version with one focused `module spider <tool>` or `module avail` query. When
   the user did not request a version, prefer the newest available stable
   version unless an existing project or worked example pins another. Report
   the selected version once; routine discovery does not need a running
   narration. One miss is not proof a tool is absent: Neurodesk module names
   are idiosyncratic, so retry with alternative spellings and acronyms before
   telling the user the tool is unavailable.
3. **Never compute in the active shell.** Do not run `datalad get`,
   neuroimaging processing, or any other substantial computation
   interactively; preserve the eventual acquisition and analysis commands in
   scripts. Lightweight discovery is the exception and may run in the shell:
   module discovery, command `--help`/version inspection, metadata-only DataLad
   clones, Git tree/tag inspection, shell syntax checks, ASTRA validation, and
   file/image metadata inspection.
4. **Use retained scripts and Slurm for substantive work.** Put scripts under
   `src/` and name every one `analysis_<step>_<description>.sh`, including
   acquisition scripts such as `analysis_00_download_data.sh`. Use one script
   per analytical step, not one per output or universe. A universe is not a
   job: batch short alternatives with identical dependencies and resources in
   one script or a Slurm array; split them only when isolation or materially
   different resources justify it.
5. **Pin execution environments.** Every neuroimaging script loads an explicit
   module version (`module load <tool>/<version>`). DataLad, Git, rclone, and
   osfclient are already in the main environment and data-only scripts do not
   load a neuroimaging module. Submit from the project root and resolve it in
   every job with `PROJECT_DIR="${SLURM_SUBMIT_DIR:-$PWD}"`; Slurm executes a
   spool copy, so `BASH_SOURCE[0]` points at `/var/spool/slurmd`, not the
   workspace.
6. **Never publish partial or stale outputs.** Write each attempt to a
   job-specific temporary file or directory on the same filesystem, validate
   that temporary artifact, and atomically rename it into its final path only
   after success. Refuse to overwrite an existing final result unless the user
   requested replacement or the old result has first been preserved. Never
   use `test -s` on a path that could have survived an earlier attempt. Remove
   or isolate failed-attempt artifacts before retrying.
7. **Submit cleanly and monitor efficiently.** Run validation first, then run
   `sbatch --parsable` as its own command. Do not chain submission behind
   linting, Git, or validation commands. For a job expected to finish within
   two minutes, `timeout 300 sbatch --parsable --wait` avoids repeated queue
   polling; `--wait` waits out queue time as well as runtime, so if the timeout
   fires the job is still queued or running and must be recovered through the
   ID `--parsable` already printed. For a longer job, poll the exact job no more
   often than every ten seconds. Queue disappearance is not success.
8. **Use a complete success predicate.** A job is submitted only when its job
   ID was captured. It succeeds only when `sacct` reports `COMPLETED` with
   `ExitCode` `0:0`, its logs have been inspected, and its expected artifacts
   were freshly produced and validated. An empty log can be valid, but it is
   never sufficient evidence by itself.
9. **Inspect the result, not merely the exit code.** Check numerical and file
   plausibility, create a correctly encoded PNG QC artifact, verify its real
   format with `identify`, and inspect it visually. For an unfamiliar command,
   inspect its actual CLI help or use a tested project example before fan-out.
   Comparative scientific claims require comparative evidence: for example,
   mask volume supports a volume difference, while a claim about where tissue
   differs requires a mask-difference or boundary overlay.
10. **Use ASTRA as the scientific record.** Document every analysis with the
    ASTRA skill, preserve existing decisions and findings, and record findings
    only after their artifacts exist and have been inspected. Report schema
    validation, script execution, and recorded run provenance as three separate
    statuses; `astra validate` checks the specification but does not execute or
    verify its recipe commands.

## Environment

- **Modules:** software is managed by Lmod. `module spider <query>` and
  `module avail` find a tool; `module help <name>` shows its usage examples.
  Load explicit versions in bash with `module load <tool>/<version>`, and in
  Python or a notebook with `import module; await module.load('tool/version')`.
- **Python:** a full Miniconda environment is available, and `mamba`, `conda`,
  `pip`, and `uv` may all install missing packages. Record any such install in
  the script that needs it or in a `requirements.txt`, so the environment stays
  reproducible.
- **Preference:** use a tool provided by `module load` over a custom
  installation unless the module genuinely does not exist.

## Fast path for ASTRA authoring

**Start from the closest worked project** rather than rebuilding a familiar
analysis from prose. For FSL BET, the canonical project is installed at
`/opt/neurodesktop/examples/astra-bet`; copy it into the writable workspace and
adapt it. Inspecting that project satisfies the initial ASTRA orientation for a
matching BET task. For other analyses, begin with the short `astra spec`
concept map, then query only an unfamiliar term with `astra spec <term>`. Do
not read the long getting-started tutorial or run `astra spec --full` when the
worked project already demonstrates the needed concepts.

**Match `version:` to the installed schema:**

```bash
python -c 'import importlib.metadata as m; print(m.version("astra-spec"))'
```

**Make each decision self-describing.** An option label carries its selected
value (`Standard (-f 0.5)`, not merely `Standard`), and the recipe
parameterizes it with `{decisions.<id>}`.

**Let recipes declare their real dependencies.** Use `{inputs.<id>}` and
`{output}` rather than hardcoded workspace paths, and give each output the
command that really produces it; identical recipes attached to several outputs
can cause a runner to submit the same work repeatedly.

**Look up findings under the right schema term.** Entries in `findings:` and
`prior_insights:` are `Insight` objects — the schema has no `Finding` class, so
read `astra spec insight`, not `astra spec finding`.

**Model acquisition provenance too.** A download step that exists only as a
script is invisible in the graph: the input appears to arrive from nowhere.
Either declare it as an output with its own recipe, or say in the input's
`description` exactly which script materializes it and from which pinned
version. A metadata-only DataLad clone may be used interactively to discover
real tags and paths, but save the final clone, checkout, and dataset-relative
`datalad get` commands in `src/analysis_00_download_data.sh`. Store data below
`data/` and use a BIDS layout when available.

**Never guess dataset contents from web-search results.** Clone the metadata
and look — `datalad clone https://github.com/OpenNeuroDatasets/<accession>.git`,
then `git -C <accession> tag` for the available versions and `find <accession>
-name '*T1w.nii.gz'` for the real paths. Cloning is cheap, because DataLad
fetches file *contents* only on `datalad get`. `ds000114` is the known-good
small OpenNeuro demonstration dataset when the user has no preference. `datalad
get` paths are relative to the cloned dataset, so use:

```bash
datalad -C data/<accession> get <path-below-the-dataset>
```

**Do not re-validate what the save hook already validated.** The hook normally
reports ASTRA validation after an edit. Run explicit validation once after a
coherent edit batch, and once after adding final findings; validate every
universe when a decision or universe changed. If the hook did not run or
reported an error, use the CLI immediately.

**Treat an existing `astra.yaml` as an accumulated scientific record.** A
follow-up request to try another defensible method normally adds an option and
universe; it does not delete prior methods, findings, evidence, outputs, or
results. Renaming or deleting their IDs requires explicit user authorization.
Findings describe completed observations, never planned or pending work, and
cite the artifact that supports the claim.

## Slurm script baseline

Use sensible resource estimates and comment only the commands whose purpose or
contract is not obvious:

```bash
#!/bin/bash
#SBATCH --job-name=<descriptive_name>
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
#SBATCH --time=HH:MM:SS
#SBATCH --mem=<X>G
#SBATCH --cpus-per-task=<N>

set -euo pipefail

# Slurm executes a spool copy; the submission directory is the project root.
PROJECT_DIR="${SLURM_SUBMIT_DIR:-$PWD}"
cd "${PROJECT_DIR}"

module load <tool>/<version>

# Write and validate job-specific temporary outputs. Publish them to their
# final paths only after every command and check succeeds.
<commands>
```

Before a Git-only check such as `git diff --check`, first run
`git rev-parse --is-inside-work-tree`. Analysis workspaces are often not Git
repositories; skip optional Git checks there without treating that as an
analysis failure or initializing a repository.

## Completion and reporting

After processing, inspect the exact accounting record, logs, declared outputs,
and unexpected leftovers. A bounded final check should establish all of:

- `sacct` state `COMPLETED` and `ExitCode` `0:0` for each submitted job or array
  task;
- fresh expected artifacts with the intended file types and dimensions;
- no partial or stale files from failed attempts mixed into final results;
- visual QC that supports no stronger claim than it displays; and
- a final `astra validate` pass after evidence-backed findings were recorded.

In the final response, link key artifacts with absolute workspace paths so
JupyterLab opens them in the main panel. State separately:

- **Specification:** whether `astra.yaml` and affected universes validate.
- **Execution:** which Slurm jobs completed and which outputs were inspected.
- **Provenance:** whether a recognised run manifest exists. Plain `sbatch`
  execution remains honestly `spec-only` in the viewer even when the scripts
  succeeded.

If the user explicitly asks for an ASTRA execution badge and the analysis has
no `container:`, ask before adapting the project to the optional `lc` path:

```bash
cd /home/jovyan/my-analysis
mkdir -p logs
NEURODESK_ASTRA_MODULES="fsl/6.0.7.22" \
NEURODESK_ASTRA_UNIVERSE="baseline" \
  sbatch /opt/neurodesktop/astra_lc_run.sbatch
```

That path writes `status.json` and reaches amber "Executed, unverified"; it
cannot produce a green verified record. Do not use it by default.
