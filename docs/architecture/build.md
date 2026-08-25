---
title: Build-time behaviors
description: Image build steps with non-obvious behavior — the Notebook
  Intelligence patches and rebuild, the MyST/RISE rebuild, the Apptainer build
  stage, and user permissions
parent: ../architecture.md
status: current
last-reviewed: "2026-08-25"
---

# Build-Time Behaviors

Part of [Architecture](../architecture.md). Build arguments are listed in
[Environment variables](../environment-variables.md#build-arguments). Two
further build-time behaviors live with their subsystems:
[config generation](webapps.md#build-time-config-generation) and
[CVMFS setup](cvmfs.md#build-time-cvmfs-setup).

## Layer Ordering and Cache

The runtime stage is ordered by how often each layer's inputs change, because
invalidating a layer re-runs every layer after it. Three bands, in order:

1. **Stable system software** — apt packages, builder-stage copies, Tomcat,
   TinyTeX, Firefox, conda/pip, and the three expensive from-source
   labextension rebuilds. Keyed on pinned versions that rarely move.
2. **Pinned-version tool installs** — CVMFS keys, neurocommand, the agent
   CLIs (codex, claude, opencode), the ACP adapters, and lightcone. Keyed
   only on explicit version/ref bumps, never on local files.
3. **Local-file layers** — the local JupyterLab extension builds
   (`extensions/`), then kernel/Guacamole/home-default configuration, and
   finally the catch-all runtime-config layer. Everything here is keyed on
   repository files that change frequently, so it sits last and re-runs
   cheaply.

Layers that consume repository files bind-mount the specific files they
install rather than whole directories: a whole-directory mount keys the layer
on every sibling file, so an unrelated edit (for example a unit test, when
all of `tests/` was mounted) would needlessly rebuild everything downstream.
When adding a layer, mount individual files and place the layer at the
band matching its most volatile input.

## Image Size Hygiene

Layers are append-only: deleting or re-owning a file in a later layer only
adds whiteouts or duplicates while the original layer keeps shipping the
bytes. The image therefore follows three rules, asserted by
`pytest /opt/tests/test_image_size_hygiene.py` in the built image:

- **Build-only packages are purged in the layer that needs them.** The pip
  layer runs as root, installs `build-essential` (gcc for sdist-only
  packages such as `traits`, which ships no cp313 wheel), runs the pip steps
  as `${NB_USER}` via `runuser`, and purges the toolchain before the layer
  ends. Node's unused C headers are deleted in the nodejs install layer.
- **Ownership and modes are set where a tree is created.** `/usr/local/tomcat`
  is chowned and made world-readable in its install layer and in the WAR
  extraction layer; a whole-tree `chown -R` in a later layer would duplicate
  ~50 MB per run.
- **Unreachable payload is stripped in the layer that installs it.** Vendored
  duplicate agent binaries (the ACP adapters' platform packages and
  `claude-agent-sdk/_bundled`, together ~750 MB), webpack/TS sourcemaps, a
  curated list of heavyweight bundled Python test suites, and Tomcat's
  default webapps are all deleted where they first appear.

## Notebook Intelligence Patches

The upstream Notebook Intelligence settings panel auto-saves its client-side
state on open, using the capabilities cache fetched at page load. That
reverts any `~/.jupyter/nbi/config.json` change made behind the server's
back — in particular the OpenCode model selection mirrored by
`nbi_setup.sh`. Until this is fixed upstream, the Dockerfile pins
`notebook_intelligence` and runs
[`config/agents/patch_nbi.py`](../../config/agents/patch_nbi.py) to rewrite the
bundled labextension so opening the settings panel first re-fetches
capabilities (the backend reloads the config file from disk to answer) and
rebuilds the panel from that fresh state.

The same patcher also fixes the server-side Ollama provider: `ollama list`
reports an empty `details.family` for models imported from safetensors/mlx,
so upstream's `f"{family}.context_length"` lookup raises and the provider
logs an ERROR and drops the model from the chat model list. The patch falls
back to the model info's sole `*.context_length` key; a model without any
such key is still skipped as before.

Both patches are anchored on the exact upstream code and fail the image
build when a `notebook_intelligence` upgrade changes it, so the workarounds
cannot silently regress; re-verify and update (or drop) them when bumping
the pin.

Notebook Intelligence 5.3.1's published frontend targets JupyterLab 4.2.
The Dockerfile therefore rebuilds the matching source tag, replaces its older
dependency graph with the checked-in, JupyterLab
4.6-compatible Yarn lockfile, installs that graph immutably, installs the
resulting federated extension, and only then applies the settings patch. The
build asserts that a `remoteEntry` bundle exists before continuing. Regenerate
`config/jupyter/notebook-intelligence-5.3.1.yarn.lock` when changing the NBI or
JupyterLab builder pins.

## MyST and RISE Extension Build

MyST is rebuilt against RISE's JupyterLab application so its markdown viewer is
available in presentation mode. MyST 2.7.0's published shared-package metadata
requests Jupyter YDoc 3.x, while the base image's JupyterLab 4.6 uses YDoc 4.x;
the source build pins that exact YDoc 4 release in both the package manifest and
the generated lockfile. RISE also retains
a Python dependency on the legacy `jupyterlab-mathjax3` package. Its JupyterLab
3-only frontend is not exposed in the final application; JupyterLab 4.6 and
RISE's standalone application both provide the current built-in MathJax
extension.

## Apptainer

The Dockerfile builds Apptainer from upstream source in a dedicated build stage
and copies `/opt/apptainer` into the runtime image. The build is controlled by
`APPTAINER_VERSION`, `APPTAINER_GO_VERSION`, and `APPTAINER_GRPC_VERSION` so the
image can move to scanner-fixed Go toolchain and module versions before a
matching upstream multi-arch runtime image is published.

macOS Docker/root sessions use `--overlay /tmp/apptainer_overlay` for writable
container sessions. This works around the "FATAL:   image targets 'amd64',
cannot run on 'arm64'" bug on macOS. Other non-Apptainer sessions leave
`neurodesk_singularity_opts` empty because it interferes with VS Code and
Matlab. Non-root Apptainer/HPC sessions use `--writable-tmpfs` because setuid
Apptainer cannot use a directory overlay as an unprivileged user.

When `/proc/driver/nvidia/version` shows that the proprietary NVIDIA driver is
loaded, `environment_variables.sh` defaults `APPTAINER_NV` to `1`. Apptainer
then binds the host's NVIDIA device files and userspace libraries into each
tool container. The libraries support both CUDA and an NVIDIA-driven display.
An NVIDIA-driven display needs the host's `libGLX_nvidia.so.0` even for an
OpenGL application that does not use CUDA. An explicit `APPTAINER_NV` value
takes precedence, so `APPTAINER_NV=0` disables the binds when a host library is
incompatible with an older container glibc.

## User Permissions

The container runs as the `jovyan` user from the base Jupyter image. The
`NB_UID` and `NB_GID` environment variables allow matching host user
permissions.
