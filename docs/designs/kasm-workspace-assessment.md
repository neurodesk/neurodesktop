---
title: Kasm workspace assessment
description: Integration options and validation needs for a Neurodesktop Kasm workspace
parent: index.md
status: research
last-reviewed: "2026-09-18"
---

# Kasm workspace assessment

Part of [Design records](index.md). This records the initial assessment.
The resulting image variant is documented in [Kasm image](../architecture/kasm.md).

## Fit with Neurodesktop

A native Kasm container workspace is feasible, but the current image needs
desktop and startup integration. The [Dockerfile](../../Dockerfile) derives
from Jupyter base-notebook and ends with `USER root`. Docker-stacks startup
hooks initialize services before switching to the notebook user. The
[desktop](../architecture/desktop.md) starts LXDE through TigerVNC or xrdp on
demand behind Guacamole and Jupyter. Its health check currently tests Jupyter.

Kasm's [core images](https://github.com/kasmtech/workspaces-core-images)
provide the desktop streaming and platform integration, including KasmVNC.
The existing Jupyter endpoint cannot substitute for that integration.

## Image strategy

There are two candidates for a native image:

- Derive a separate image from a pinned Neurodesktop release and integrate
  Kasm's runtime. This preserves our software build but makes us responsible
  for matching Kasm's startup, authentication, profile, and service behavior.
- Derive from a Kasm core image and reuse Neurodesktop's installation steps.
  This follows Kasm's custom-image approach but requires separating our
  software installation from Jupyter base-image assumptions.

The proposed first experiment is the Neurodesktop-derived image because the
requested foundation is our existing container. Treat the runtime integration
as a feasibility test, not an already-supported thin wrapper. Match the Kasm
runtime to the deployment version. Keep the normal Neurodesktop image as a
separate build target.

Kasm's [custom-image guide](https://docs.kasm.com/docs/how-to/workspaces-sessions/container-workspace/customization/building-images)
uses `/home/kasm-default-profile` for defaults, `/home/kasm-user` for the
session home, and UID 1000 for the final user. Its custom startup hook runs as
that user. These conventions need reconciliation with our root bootstrap and
`jovyan` home. Check the deployed release before adopting upstream scripts.

## Work involved

The Kasm image needs one owner for desktop startup. KasmVNC would launch LXDE
without also starting the TigerVNC or Guacamole desktop path. Adapt the
desktop clipboard setup, environment initialization, readiness checks, and
shutdown behavior to that path. If Jupyter remains available, give it an
explicit startup and authentication design; opening it in the session's
browser is a smaller first scope than exposing another external web endpoint.

Resolve user IDs and home paths explicitly. Neurodesktop uses `jovyan`,
`NB_UID`, and defaults under `/opt/jovyan_defaults`. Kasm profile handling must
restore the expected desktop configuration and preserve ownership on mounted
homes. Keep user data separate from the shared software cache.

The main runtime uncertainty is [CVMFS and Neurocommand](../architecture/cvmfs.md)
together with [nested Apptainer execution](../architecture/build.md#apptainer).
The current Docker CI includes privileged execution. A working desktop alone
does not prove that neuroimaging tools can run under Kasm's container settings.
Compare a read-only host CVMFS mount with an in-session FUSE mount, then test
Apptainer separately. A host CVMFS mount does not by itself solve Apptainer's
namespace, mount, or security-policy requirements.

Packaging also needs an image tag, workspace definition, icon, CPU and memory
settings, shared-memory allocation, storage mounts, and optional GPU settings.
Choose resource limits from measured workloads. GPU visibility, desktop
rendering, and GPU use inside Apptainer need separate checks.

## Evidence needed before release

Start with one session, one module, and one representative neuroimaging GUI
under the intended permissions. Then verify clipboard, resize, reconnect,
session deletion, a fresh home, a persistent home, and isolation between two
users. Verify file access and any retained Jupyter services. Add GPU and
additional architectures only after the basic path works.

Implementation tests belong in [the existing two tiers](../testing.md):
checkout-runnable tests in `tests/unit/`, image-dependent tests in
`tests/container/`. A real Kasm session is still required to verify platform
authentication and lifecycle behavior.

## Deployment and a smaller alternative

Register the built image directly as a Container Workspace for testing.
[Workspace settings](https://docs.kasm.com/docs/how-to/workspaces-sessions/container-workspace/workspaces)
include resource limits and Docker Run Config for devices, capabilities,
security options, and shared memory. These settings can express runtime
requirements; they do not prove the nested tools work on a particular host.
[Persistent profiles](https://docs.kasm.com/docs/how-to/data-storage/persistent-profiles)
and [volume mappings](https://docs.kasm.com/docs/how-to/data-storage/volume-mapping)
provide separate mechanisms for homes and datasets. Give each user's profile
a distinct path.

A [workspace registry](https://docs.kasm.com/docs/how-to/workspaces-sessions/container-workspace/workspace-registry)
is an optional catalog for distributing the workspace definition and icon.
It is separate from the Docker registry that stores the image layers.

For a smaller initial integration, Kasm's
[Server Workspace approach](https://docs.kasm.com/docs/1.19.0/how-to/workspaces-sessions/server-workspace/fixed-infrastructure)
can connect to a running Neurodesktop through raw VNC. The endpoint needs to
start independently of our on-demand Jupyter desktop launcher and have
appropriate network access and authentication. Kasm does not manage that
external container's lifecycle. This provides browser access but does not
meet the same per-session provisioning goal as a native container workspace.
