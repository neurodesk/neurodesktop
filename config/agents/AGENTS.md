# Neurodesk Agent Context

## Critical Rules

1. **NEVER run neuroimaging tools or downloads of data directly.** ALWAYS write a bash script that uses `module load <tool>/<version>` with an explicit version pinned. Or use osf/datalad inside a script to fetch data!
2. **Discovery before execution.** Run `module avail` or `module spider <tool>` to confirm a tool exists and check available versions before writing any script. If the exact name doesn't exist check for alternative spellings and acronyms. Tell the user about this. Datalad, Git, rclone and the osfclient are installed in the main environment and do not to be loaded!
3. **Name scripts consistently:** `analysis_<step>_<description>.sh` (e.g., `analysis_01_skull_strip.sh`, `analysis_02_registration.sh`) and store them under the subdirectory src following the astra specification.
4. **Submit to SLURM, don't run interactively.** Neuroimaging jobs are long-running — submit via `sbatch`, then monitor with `squeue` and inspect log files. Metadata inspection, module spider, git ls-tree, ASTRA validation, and shell syntax checks are allowed to be run directly!
5. Always ask the user which tool to use when there are multiple choices
6. Always use the astra skill to document analysis in an astra.yaml spec.
7. **Submit jobs as their own command.** NEVER chain `sbatch` behind validation, linting, or git commands with `&&`.
8. **Confirm, don't assume.** A job is submitted only when you have its job ID; it is finished only when you have read its log. Never report an analysis as run, complete, or successful on the strength of having written the script.
9. **Resolve the project through Slurm's submission directory.** Submit from the project root and set `PROJECT_DIR="${SLURM_SUBMIT_DIR:-$PWD}"` inside every job. Slurm executes a spool copy of the script, so deriving the project from `BASH_SOURCE[0]` points at `/var/spool/slurmd`, not the workspace.


## Workflow

1. **Plan** — Identify the analysis steps. Clarify tool choices with the user.
2. **Write script** — One bash script per analysis step, with `module load`, explicit versions, and comments explaining each command.
3. **Submit** — Use `sbatch` with appropriate resource requests (`--time`, `--mem`, `--cpus-per-task`). Include SLURM directives in the script header. Run the `sbatch` on its own, after any checks have passed on their own.
4. **Monitor** — Check job status (`squeue -u $USER`) and tail log files for errors.
5. **Validate** — Once complete, check outputs for plausibility. Generate a PNG visualization of results (e.g., overlay segmentation on anatomical, render surfaces) and inspect it. Flag anything that looks wrong.
6. **Record what you learned** — Write what the QC actually showed back into `astra.yaml` as a `findings:` entry citing the artifact. A spec with no findings has an empty Evidence view no matter how well the analysis ran.
7. Treat an existing astra.yaml as an accumulated scientific record. A follow-up request to try or use another defensible method adds a decision option and universe by default; it does not remove prior methods, findings, evidence, outputs, or results. Renaming or deleting existing decision, output, universe, insight, or finding IDs requires explicit user authorization. Findings describe completed observations. Never create a future-tense or “pending” finding before its artifact has been produced and inspected.


## Environment & Tooling

* **Module System:** This environment uses **Lmod Modules** to manage software.
    * *Search:* Use `module spider <query>` or `module avail` to find tools.
    * *Info:* Run `module help <module name>` for usage examples.
    * *Loading Modules in Bash:* Always use explicit versioning: `module load <toolname>/<version>`.
    * *Loading Modules in Python/Jupyter:* Use the specific snippet: `import module; await module.load('toolname/version')`.
* **Python:** You have a full Miniconda environment. You may use `mamba`, `conda`, or `pip` to install missing packages. You can also use uv for managing packages.

## Workflow Standards

### Tool Selection

* **Trade-off Analysis:** Neuroimaging often offers multiple tools for one task (e.g., FSL vs. ANTs for registration).
* *Rule:* Before writing code, list the available options, explain the trade-offs (speed, accuracy, input requirements, licensing) to the user, and ask for a decision.
* *Preference:* Prioritize tools available via `module load` over custom installations unless necessary.

### B. Scripting & Reproducibility

* **Naming Convention:** ALL analysis scripts must follow: `analysis_<step_number>_<summary>.sh` (e.g., `analysis_01_brain_extraction.sh`).
* **Bash Strategy:**
    * **NEVER** run heavy neuroimaging commands directly in the active shell.
    * **ALWAYS** wrap them in a Bash script including the necessary `module load` commands.
    * Document any `pip/conda` package installations inside the script comments or a separate `requirements.txt`.
* **Data Management:**
    * Use **DataLad** for downloading sample data (e.g., from OpenNeuro).
    * Store data in the data sub-directory.
    * Save the DataLad download commands in a script (e.g., `00_download_data.sh`) to ensure full reproducibility.
    * Use **BIDS-compliant** directory structures where possible.
    * **Do not guess dataset contents from a web search.** Clone the metadata
      first and look, e.g. `datalad clone https://github.com/OpenNeuroDatasets/<accession>.git`
      then `git -C <accession> tag` for the available versions and `find
      <accession> -name '*T1w.nii.gz'` for the real paths. Cloning is cheap —
      DataLad fetches file *contents* only on `datalad get`. Pinning a tag or a
      path you have not seen produces a script that fails at runtime.
    * `ds000114` is a known-good small OpenNeuro dataset if the user has no
      preference and you just need a scan to demonstrate a pipeline.
    * **`datalad get` paths are dataset-relative, not workspace-relative.** If
      you clone into a subdirectory, run the get from inside the clone or with
      `datalad -C <dataset_dir> get <path_below_that_dir>`. Passing a path that
      resolves outside the clone fails with `path not associated with dataset`,
      the clone still succeeds, and the script looks like it worked until
      something tries to read the file.


### Execution & Validation
1.  **Submit to SLURM:** Do not run heavy scripts on the login node. Generate an `sbatch` header for the script and submit it.

Slurm Script Template - fill in sensible guesses for time,mem,cpu need!
```bash
#!/bin/bash
#SBATCH --job-name=<descriptive_name>
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
#SBATCH --time=HH:MM:SS
#SBATCH --mem=<X>G
#SBATCH --cpus-per-task=<N>

# Slurm executes a spool copy; the submission directory is the project root.
PROJECT_DIR="${SLURM_SUBMIT_DIR:-$PWD}"
cd "${PROJECT_DIR}"

# Load required software
module load <tool>/<version>

# Create output directory
mkdir -p <output_dir>

# Run analysis
<commands>
```
2.  **Monitor:** Instruct the user on how to check the queue (`squeue`) and inspect log files.
3.  **Quality Control (QC):**
    * Once processing is complete, check results for plausibility.
    * **Visual QC:** Generate a PNG snapshot of the result (e.g., using Python plotting) so the user can verify the analysis worked.

### Authoring the ASTRA analysis

**Start from the worked example, not from a blank file.** A complete, valid
FSL BET analysis — decisions, recipes, universes, prior insights, and findings
— is installed at `/opt/neurodesktop/examples/astra-bet`. Read it before
writing a spec, and copy it as a starting point when the analysis is close.

**Read the schema selectively.** `astra spec <term>` (e.g. `astra spec output`,
`astra spec decision`, `astra spec insight`) documents one concept. Do NOT run
`astra spec --full` — it dumps the entire reference and buries the rest of the
session in text you did not need.

**Match the installed spec version.** Set the spec's `version:` to the
installed `astra-spec` version, which you can read with:

```bash
python -c 'import importlib.metadata as m; print(m.version("astra-spec"))'
```

**Make decisions self-describing.** An option's label must carry the value it
selects (`Standard (-f 0.5)`, not `Standard`) so the spec can be read without
opening the shell script it maps onto.

**Let recipes declare their real dependencies.** Use the `{inputs.<id>}` and
`{output}` placeholders rather than hardcoding paths inside the script, and give
each output the command that actually produces it. Three outputs sharing one
identical `sbatch` line means a runner submits that job three times.

**Model the data retrieval too.** A download step that exists only as a script
is invisible in the graph — the input appears to arrive from nowhere. Either
declare it as an output with its own recipe, or say in the input's
`description` exactly which script materializes it and from which pinned
version.

**Record findings after QC.** Once you have inspected the QC image, write what
it showed into `findings:`, citing the artifact it rests on. The entries in
`findings:` are `Insight` objects — the schema has no `Finding` class, so look
them up with `astra spec insight`, not `astra spec finding`:

```yaml
findings:
  extraction_is_clean:
    claim: "BET at -f 0.5 removed skull and neck without clipping cortex."
    created_at: "2026-07-31T10:00:00Z"
    evidence:
      - id: ev_qc
        artifact: qc_mosaic
```

This is the step that populates the viewer's Evidence view. Skipping it leaves
the analysis with nothing to show there, however well it ran.

After changing astra.yaml, it should also:
      - Validate every universes/*.yaml.
      - Warn when existing decision/output/finding IDs disappear.
      - Warn when the number of findings or evidence records decreases.
      - Warn when a completed finding is replaced by a pending claim.
      - Report newly unreferenced scripts or result directories.

On follow-up turns, inspect and preserve the existing project before proposing a replacement. User criticism of one result is evidence for a new alternative or finding, not permission to discard the previous analysis.

### Reporting an ASTRA analysis

When you finish work on an `astra.yaml` and `astra validate` passes, summarise
the analysis in your reply and link the key artifacts. Link files you want the
user to open as absolute paths (e.g.
`[QC overlay](/home/jovyan/project/results/qc.png)`); JupyterLab opens
absolute workspace paths in the main panel, while a relative path will not
resolve from a chat message.

If the user explicitly wants the badge to reflect a real run, there is one
supported way to get it, and only if the analysis declares no `container:`:

```bash
cd /home/jovyan/my-analysis        # the directory holding astra.yaml
mkdir -p logs
NEURODESK_ASTRA_MODULES="fsl/6.0.7.22" \
NEURODESK_ASTRA_UNIVERSE="baseline" \
  sbatch /opt/neurodesktop/astra_lc_run.sbatch
```

That template runs `lc run` inside the allocation and writes `status.json`
beside `astra.yaml`, which the viewer picks up on Refresh. The badge becomes
amber "Executed, unverified" and stays amber; nothing makes it green. Do not
reach for this by default — it requires every recipe to be a real ASTRA recipe
`lc` can materialize, which is a larger change than writing the analysis as
ordinary step scripts. Ask the user first.

## Critical Constraints
* **DO NOT** assume a module is loaded; always load it explicitly in the script.
* **DO NOT** hardcode absolute paths specific to temporary sessions; use relative paths or defined variables.
