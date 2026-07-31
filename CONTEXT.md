# Neurodesktop Scientific Workflow Integration

Vocabulary for describing ASTRA specifications, Lightcone runs, and Neurodesk
tool execution without overstating what was executed or verified.

## Language

**Selected analysis**:
An ASTRA specification interpreted under one selected universe. It describes
scientific intent and is not an execution claim unless linked to a run record.
_Avoid_: Run, executed workflow

**Verified run**:
A materialized analysis execution whose outputs and actual execution
environment have passed the integration's integrity and provenance checks.
_Avoid_: Reproducible run, verified analysis

**Module-mediated execution**:
Tool execution through an explicitly versioned Neurodesk module whose command
surface delegates to its resolved software container.
_Avoid_: Native execution, host execution

**Provisional module run**:
A module-mediated run that demonstrates workflow behavior without attesting
the actual container selected by the module wrapper. It may report output
integrity separately, but it is not a verified run.
_Avoid_: Production run, verified run

**Worked ASTRA example**:
The specification-only BET threshold analysis shipped at
`examples/astra-bet`, provided as a starting point to copy and edit. It
declares intent and has not been executed; it is not a reference result.
_Avoid_: Reference analysis, ground-truth workflow, verified run

**In-image Slurm execution**:
Work submitted to Neurodesktop's own single-node Slurm scheduler inside the
running image. It is not evidence of external-cluster or host-scheduler
compatibility.
_Avoid_: External Slurm validation, host-mode Slurm, HPC execution

**Lightcone-managed container execution**:
Tool execution for which Lightcone directly selects and launches the container
runtime represented in the run record.
_Avoid_: Module-mediated execution

**Truthful provenance**:
A run record that identifies the scripts, modules, container artifacts, inputs,
and outputs that actually participated in execution.
_Avoid_: Declared provenance
