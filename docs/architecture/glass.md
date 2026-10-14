---
title: Glass runtime
description: Opt-in CrumbleCracker native desktop in the standard Neurodesktop image
parent: ../architecture.md
status: current
last-reviewed: "2026-10-14"
---

# Glass runtime (experimental)

The standard image includes native Xorg, systemd and the Glass clipboard/resize
helpers. Docker/Podman/HPC startup remains the default. `NEURODESKTOP_RUNTIME=glass`
selects VM-specific initialization; it does **not** boot a VM or select a hypervisor.
GPU acceleration is not part of this initial integration.

## CC deployment

Use a locally built candidate image and record its exact platform digest. Boot
with `InitSystem=systemd`, the `jovyan` account (UID 1000 / GID 100), and display,
vsock and input devices supplied by CC. In the headless API request set:

```json
"env": {"NEURODESKTOP_RUNTIME": "glass"}
```

CC injects its transient headless target and starts
`neurodesktop-glass.service` on demand. For an explicit standalone guest session,
start `glass.target`; nothing enables it as the image's default target. Normal
container startup still uses the inherited docker-stacks entrypoint. The mode
must be included in CC's environment overrides for all services; do not set it
to `container` when invoking Glass.

Host-managed CVMFS is recommended: CC supplies `CVMFS_DISABLE=true` and
`NEURODESKTOP_CVMFS_STARTUP_MODE=external` and mounts the repository. Disabled
CVMFS also works with local tool artifacts. The Glass service defaults to no
guest-managed mount; opt-in guest mounting uses the existing startup settings.

Root preparation reuses the standard startup hooks and package-only sudo policy,
skips xrdp/password provisioning, and never deletes VNC/Guacamole state. Home
restoration and workspace quarantine still run. `/vmsh-neurodesktop-storage`
is mapped to `/neurodesktop-storage` when mounted; conflicting nonempty storage
or an unrelated existing symlink fails rather than replacing user data.

Each nested scientific command uses Apptainer's `--writable-tmpfs` session.
Changes inside one tool image disappear when that command exits and are isolated
from other tool invocations. Files in bound home and storage directories persist.
The read-only CPU-information bind and the user's MATLAB cache remain available.

The clipboard helper samples an external X11 clipboard owner every 200 ms,
so copying new text from the same application updates the host clipboard.
A requesting window can close during a paste without terminating the helper.
The helper continues serving the last host clipboard while the relay is offline.
Reconnecting preserves that text until a new host frame or guest clipboard owner
replaces it; guest changes made offline are sampled after reconnection.
Legacy `STRING` requests convert UTF-8 host text to Latin-1, as required by the
[X11 selection contract](https://www.x.org/releases/current/doc/xorg-docs/icccm/icccm.pdf).
Characters outside Latin-1 cause an explicit conversion refusal; `UTF8_STRING`
requests preserve the original text.
Clipboard conversions use direct X11 properties and reject text that exceeds
the X server's request capacity. Incremental (`INCR`) selection transfers are
outside this experimental implementation.

## Jupyter and security

The desktop's **JupyterLab (host browser)** shortcut starts the static
`neurodesktop-jupyter.service` using a sudo rule for that exact command only.
It does not allow arbitrary service management, root preparation or sudo shell
access. This narrow activation permission is separate from `GRANT_SUDO`'s generic
package/no/full policy. Service files and root preparation are root-owned.

Jupyter uses a random token stored mode 0600 in the user's private runtime
directory. Tokens are not passed on command lines or in probe URLs. Glass startup
skips the container's access-URL watcher, which would otherwise print the token
to the service journal. CC's current
browser callback opens `/lab` without a token, so the shortcut displays the token
in the user's terminal for the login page. It does not disable authentication.
The product forwards guest port 8888; changing the Jupyter port also requires
matching host-runtime forwarding. Inspect service journals for startup failures.

The image healthcheck retains Jupyter health in container mode and checks the
X display socket in Glass mode, without requiring on-demand Jupyter to be running.

The root Glass session manages Xorg and the user LXDE session in one systemd
control group. Stopping/restarting the service stops its child processes, including
clipboard/resize helpers. Jupyter is separate and starts only on request.

## Acceptance

Checkout regressions run the compiled clipboard and resize helpers against
test-owned transport endpoints. Clipboard checks use a real Xvfb display.
The resize checks verify message handling and the `xrandr` command boundary;
native display resizing still requires CC acceptance.

Source/controller tests are not native VM acceptance. Before promoting an image,
verify a locally built candidate on Ubuntu/KVM and Apple Silicon: headless boot
without X, Glass frame presentation, clipboard/resize, on-demand authenticated
Jupyter, nested scientific tool execution with CVMFS on/off, shared-folder writes,
and graceful shutdown/restart persistence. Retain the normal Docker security,
startup and image-test profiles. Never test against a user's live cache/home.
