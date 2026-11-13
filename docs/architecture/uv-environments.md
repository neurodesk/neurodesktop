---
title: uv notebook environments
description: Opt-in folder environments and explicit Jupyter kernel registration
parent: ../architecture.md
status: current
last-reviewed: "2026-11-05"
---

# uv notebook environments

The image installs `neurodesk-uv` at `/usr/local/bin/neurodesk-uv` for opt-in,
project-local Python environments. It runs with the image's isolated Python
(`/opt/conda/bin/python3 -I`), even when a user environment is activated.
The default Jupyter kernel and Conda environments remain independently managed.
The feature is experimental; users explicitly select its registered kernels.

Source: [uv_kernel_prototype.py](../../config/jupyter/uv_kernel_prototype.py).
The [prototype design record](../designs/uv-notebook-prototype.md) records the
initial review and checkout-only implementation.

## Try it

Run as the notebook user inside a Neurodesktop image containing `neurodesk uv`.
Older images need updating; updating this repository does not change an existing
container. Initial setup needs access to the package index.

```bash
mkdir -p ~/analyses/demo
neurodesk uv init ~/analyses/demo
```

Select **Python (uv prototype: demo)** in JupyterLab's kernel selector. A project
gets `pyproject.toml`, `uv.lock`, `.venv`, a project-level
`.neurodesk_uv_prototype.json` ownership record, and one user kernelspec. The default
kernel dependency is `ipykernel==6.31.0`, matching the image's compatibility
choice. An existing explicit dev dependency on ipykernel is retained.

Add packages from a terminal and restart the kernel after changing dependencies:

```bash
cd ~/analyses/demo
uv add numpy nibabel
```

Dependency edits belong in the manifest and lock. `%pip` is not provided by
default; ad hoc installations are not captured in the lock. Widgets also need
their Python packages in the project environment.

Stop every kernel using the environment before rebuilding. Rebuild deletes
only a uv-created `.venv` owned by this prototype and uses `uv sync --locked`.
The ownership record lives outside `.venv` so ordinary uv operations cannot
erase it when recreating the interpreter. Keep the record with the project
when moving it; its absolute path is local state, not a portable dependency file.
Older installations can recover through their matching user kernelspec even
if uv has erased their original in-environment marker. Run `rebuild` with
the updated helper at the original path before moving such an installation.
For a moved folder, rebuild at the new path, select the new kernel, and remove
the old registration using its old path:

```bash
neurodesk uv rebuild ~/analyses/new-location
neurodesk uv remove ~/analyses/demo
```

Removal retains project files and the environment. A missing registration
returns exit status 1 and names the requested path instead of reporting success.
Initialization creates a missing lockfile even when the project already declares
ipykernel, while preserving an existing lock during synchronization. Preserve the project folder
and Jupyter's user data directory across container replacement. uv uses its
normal cache and Python installation locations; keep those persistent for
warm-cache rebuilding. The prototype uses copy mode for cache-to-venv installs
so cache and project may be on separate filesystems.

## Boundaries

- Existing unmanaged or symlinked `.venv` directories are refused.
- uv workspaces, including folders under ancestor workspace manifests, are
  refused. Ordinary nested analysis projects use `uv init --no-workspace`.
- Root, home, and mount-root projects are refused.
- A Jupyter-data-directory lock serializes prototype commands. Kernelspec writes
  are atomic, and other tools' registrations are not replaced.
- A kernel starts the prepared interpreter directly; no package resolution,
  network access, or lockfile edits occur at launch. Missing or moved environments
  produce rebuild instructions. Manual dependency-file edits require explicit
  synchronization; startup does not detect every kind of dependency drift.
- The host wrapper initializes Neurodesk before the launcher clears Python and
  Conda environment variables. It retains module paths and prepends `.venv/bin`
  to PATH, disables user site packages, and sets `VIRTUAL_ENV`.
- The launcher and host Python are image-owned. A folder menu action, automatic
  discovery, and active-kernel rebuild protection remain follow-up work.

## Migrate downloaded helpers

Stop affected kernels and run `neurodesk uv init PROJECT` on each existing
project. This preserves its identity and updates its registration to the installed
launcher. Keep the downloaded helper until every registration using it has been
updated. For an environment needing recovery, use `neurodesk uv rebuild PROJECT`
at its original location before moving it.

## Validation

Run `pytest tests/unit/test_uv_kernel_prototype.py` in a checkout and
`pytest /opt/tests/test_uv_kernel_image.py` as the notebook user in a built image.
The installed checks execute the CLI with a conflicting Python environment,
start a real kernel through the Neurodesk wrapper, verify isolation and module
initialization, and rebuild a moved project offline from a warm cache.
