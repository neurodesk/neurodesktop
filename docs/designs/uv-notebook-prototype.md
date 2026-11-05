---
title: uv notebook environment prototype
description: Opt-in folder environments and explicit Jupyter kernel registration
parent: index.md
status: implemented
last-reviewed: "2026-11-04"
---

# uv notebook environment prototype

This record describes the original checkout-only prototype. For the installed
`neurodesk-uv` command, see [uv environments](../architecture/uv-environments.md).

The original prototype tests whether an analysis folder can own a locked
Python environment while Neurodesk continues to own JupyterLab. It was not
installed by the Dockerfile and does not change the default kernel.

Claude Opus 5.5 reviewed the conceptual proposal and recommended a CLI first,
explicit kernel registration, environment cleanup after host initialization,
and no dependency synchronization during kernel startup. The implementation
uses the existing Neurodesk kernel wrapper, followed by a Python launcher in
[uv_kernel_prototype.py](../../config/jupyter/uv_kernel_prototype.py).

## Try it

Run as the notebook user inside Neurodesk, with this checkout kept at a stable
path. The CLI requires Python 3.11+ with `jupyter_core`, and uv on PATH. The
image's Python supplies those requirements.

```bash
mkdir -p ~/analyses/demo
/opt/conda/bin/python config/jupyter/uv_kernel_prototype.py init ~/analyses/demo
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
/opt/conda/bin/python config/jupyter/uv_kernel_prototype.py rebuild ~/analyses/new-location
/opt/conda/bin/python config/jupyter/uv_kernel_prototype.py remove ~/analyses/demo
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
- The launcher path and host Python path currently refer to this checkout.
  Keep both available while registrations exist. Image installation, a folder
  menu action, automatic discovery, and active-kernel rebuild protection are
  follow-up work.

## Validation

Run the boundary tests with:

```bash
pytest tests/unit/test_uv_kernel_prototype.py
```

Regression tests cover environment cleanup, missing/moved environments, distinct
names for identical folder basenames, refusal of unmanaged environments/workspaces,
non-destructive removal, and repeat initialization with explicit uv targeting.
The post-merge Opus review added coverage for uv replacing an environment, legacy
registration recovery, initialization without a lock, moved-folder rebuilds,
refusal to delete non-uv or symlinked environments, and missing registrations.

A real uv 0.12.18 smoke run created an environment with ipykernel 6.31.0,
registered it in an isolated Jupyter data directory, and launched it through
`jupyter_client.KernelManager`. Executed code confirmed its interpreter prefix,
a wrapper initialization marker, and absence of `PYTHONPATH` and `CONDA_PREFIX`.
Repeat init preserved manifest and lock bytes. A renamed folder failed to launch,
rebuilt with `UV_OFFLINE=1`, and retained manifest and lock bytes. Removing its
registration retained the environment. This used a test wrapper; installed-image
CVMFS, default/Conda kernel regression, and widget checks remain outstanding.

The focused prototype and existing container-kernel suites passed together
(27 tests). Repository-wide unit and quality results belong in the PR's
validation record; the smoke result alone does not establish those gates.

The recovery smoke test used real uv 0.12.18 with an existing manifest and no
lock, broke the interpreter link, and invoked `uv add` to recreate the environment.
Rebuild recovered both the new project-level record and an older installation
whose registration was its remaining ownership evidence. A real Jupyter kernel
confirmed interpreter isolation and wrapper initialization afterward. The same
run rebuilt a moved folder offline while preserving the manifest and lock.
