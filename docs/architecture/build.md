---
title: Build-time behaviors
description: Image build steps with non-obvious behavior — the Notebook
  Intelligence patch and rebuild, the MyST/RISE rebuild, the Apptainer build
  stage, and user permissions
parent: ../architecture.md
status: current
last-reviewed: "2026-07-31"
---

# Build-Time Behaviors

Part of [Architecture](../architecture.md). Build arguments are listed in
[Environment variables](../environment-variables.md#build-arguments). Two
further build-time behaviors live with their subsystems:
[config generation](webapps.md#build-time-config-generation) and
[CVMFS setup](cvmfs.md#build-time-cvmfs-setup).

## Notebook Intelligence Settings Patch

The upstream Notebook Intelligence settings panel auto-saves its client-side
state on open, using the capabilities cache fetched at page load. That
reverts any `~/.jupyter/nbi/config.json` change made behind the server's
back — in particular the OpenCode model selection mirrored by
`nbi_setup.sh`. Until this is fixed upstream, the Dockerfile pins
`notebook_intelligence` and runs
[`config/agents/patch_nbi.py`](../../config/agents/patch_nbi.py) to rewrite the
bundled labextension so opening the settings panel first re-fetches
capabilities (the backend reloads the config file from disk to answer) and
rebuilds the panel from that fresh state. The patcher is anchored on the
exact minified code and fails the image build when a `notebook_intelligence`
upgrade changes the bundle, so the workaround cannot silently regress;
re-verify and update (or drop) the patch when bumping the pin.

Notebook Intelligence 5.3.0's published Python wheel omits its compiled
JupyterLab frontend. The Dockerfile therefore rebuilds the matching source tag,
replaces its older dependency graph with the checked-in, JupyterLab
4.6-compatible Yarn lockfile, installs that graph immutably, installs the
resulting federated extension, and only then applies the settings patch. The
build asserts that a `remoteEntry` bundle exists before continuing. Regenerate
`config/jupyter/notebook-intelligence-5.3.0.yarn.lock` when changing the NBI or
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

## User Permissions

The container runs as the `jovyan` user from the base Jupyter image. The
`NB_UID` and `NB_GID` environment variables allow matching host user
permissions.
