---
title: T3 Code remote access
description: Headless T3 Code lifecycle, provider paths, persistent state, and
  desktop connection procedures
parent: ../architecture.md
status: current
last-reviewed: "2026-09-10"
---

# T3 Code remote access

Part of [Architecture](../architecture.md). The image includes a pinned T3 Code
server. A Jupyter Server extension starts it as the notebook user when
`NEURODESKTOP_T3_CODE_ENABLE=1`.

## Connect through a published Docker port

Publish T3 only on the Docker host's loopback interface:

```bash
docker run \
  -e NEURODESKTOP_T3_CODE_ENABLE=1 \
  -e NEURODESKTOP_T3_CODE_HOST=0.0.0.0 \
  -p 127.0.0.1:8888:8888 \
  -p 127.0.0.1:3773:3773 \
  neurodesktop:latest
```

The repository's [`build_and_run.sh`](../../build_and_run.sh) uses these T3
settings in its normal interactive mode.

After Neurodesktop starts, open a JupyterLab terminal and run:

```bash
t3 pair
```

In the T3 Code desktop app, open **Settings → Connections → Add environment**.
Use `http://127.0.0.1:3773` as the host and enter the one-time token printed by
`t3 pair`. Each new desktop device needs a fresh token.

If Docker runs on another machine, forward the host's loopback port through
SSH before pairing:

```bash
ssh -N -L 3773:127.0.0.1:3773 user@docker-host
```

## Connect through T3 Connect

T3 Connect uses an outbound managed connection, so it does not need a
published port. Enable the sidecar with its default loopback host. In a
Neurodesktop terminal, run:

```bash
t3 connect link --headless
```

Complete the browser authorization and restart the Neurodesktop instance. Sign
in to the same T3 Connect account in the desktop app, then select the linked
environment. The restart is required because T3 reads the saved link when the
server starts.

T3 Connect is the supported route for an HPC allocation when the site permits
its outbound traffic. Desktop-managed SSH to a cluster login node starts T3 on
that login node. It does not enter the Neurodesktop allocation.

## Lifecycle and state

[`neurodesk_t3_code`](../../extensions/t3-code-server/neurodesk_t3_code/)
is a Jupyter `ExtensionApp`. It owns one T3 process group, waits for the fixed
port, restarts a failed server up to five times, and stops the process group
when Jupyter stops. A port conflict parks the sidecar and leaves Jupyter
available.

The extension runs T3 only as the notebook user. It refuses uid 0 and a home
directory owned by another uid. T3 stores its database, connection identity,
and provider settings under `~/.t3`, which survives when the Neurodesktop home
is mounted.

T3 creates a one-time owner token during server startup. The extension sets
warning-only logging, disables trace output, and sends startup output to
`/dev/null`, so this credential does not enter the Jupyter or container log.
Users create pairing tokens explicitly with `t3 pair`.

## Providers

T3 discovers provider commands through
[`config/agents/t3-provider-bin`](../../config/agents/t3-provider-bin/). These
quiet launchers call the image-owned Codex, Claude, and OpenCode binaries
without the interactive Neurodesktop wrapper output. The Codex launcher still
copies `/opt/AGENTS.md` into a project when `AGENTS.md` is absent.

Provider authentication belongs to the Neurodesktop instance. Configure the
remote environment under **Settings → Providers** in T3 Code. Existing CLI
authentication under the persistent home is available to the same binaries.

The desktop app can warn when its server protocol differs from the image's
pinned T3 version. Update the Neurodesktop image to update the server rather
than using T3's systemd service installer or self-update flow.

## Build and architecture limits

The Dockerfile installs the lockfile under `config/agents/t3-code` into
`/opt/t3-code`. The install layer compiles and tests `node-pty`, then removes
foreign prebuilds and the Claude SDK's duplicate platform binary. Both amd64
and arm64 images must pass the installed server and PTY checks. T3 0.0.40 does
not include an arm64 resource monitor, so arm64 can omit resource telemetry;
server, terminal, pairing, and provider behavior must still pass.
