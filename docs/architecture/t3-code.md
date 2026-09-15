---
title: T3 Code remote access
description: Headless T3 Code lifecycle, provider paths, persistent state, and
  desktop connection procedures
parent: ../architecture.md
status: current
last-reviewed: "2026-09-14"
---

# T3 Code remote access

Part of [Architecture](../architecture.md). The image includes a pinned T3 Code
server. A Jupyter Server extension starts it as the notebook user when
`NEURODESKTOP_T3_CODE_ENABLE=1`.

## Connect through a published Docker port

Publish T3 only on the Docker host's loopback interface. Use host port 3774
to avoid conflicting with the desktop app's local server on port 3773;
the container server still listens on port 3773:

```bash
docker run \
  -e NEURODESKTOP_T3_CODE_ENABLE=1 \
  -e NEURODESKTOP_T3_CODE_HOST=0.0.0.0 \
  -p 127.0.0.1:8888:8888 \
  -p 127.0.0.1:3774:3773 \
  neurodesktop:latest
```

The repository's [`build_and_run.sh`](../../build_and_run.sh) uses these T3
settings in its normal interactive mode.

After Neurodesktop starts, open a JupyterLab terminal and run:

```bash
t3 pair
```

In the T3 Code desktop app, open **Settings → Connections → Add environment**.
Use `http://127.0.0.1:3774` as the host and enter the one-time token printed by
`t3 pair`. Each new desktop device needs a fresh token.

If Docker runs on another machine, forward the host's loopback port through
SSH before pairing:

```bash
ssh -N -L 3774:127.0.0.1:3774 user@docker-host
```

## Connect through Tailscale inside the container

The image includes `tailscale` and `tailscaled` in `/usr/local/bin` for amd64
and arm64. The Dockerfile downloads the pinned official static archive,
verifies its architecture-specific SHA-256 checksum, and installs only those
two binaries. It does not install a system service or start a daemon.

For a Kubernetes pod without a public IP, run Tailscale as the notebook user
in [userspace mode](https://tailscale.com/docs/concepts/userspace-networking).
This needs no sidecar, `/dev/net/tun`, or network administration capability.
The pod must permit outbound access to Tailscale's coordination and relay
services; see [Tailscale firewall requirements](https://tailscale.com/docs/reference/faq/firewall-ports).
Your desktop must also be connected to the tailnet and allowed to reach this
device by its access policy.

Enable T3 with `NEURODESKTOP_T3_CODE_ENABLE=1` when starting Neurodesktop.
Its default host, `127.0.0.1`, works for this setup. In a JupyterLab terminal,
run the guided setup as the notebook user:

```bash
neurodesktop-t3-setup
```

The [setup script](../../scripts/t3_tailscale_setup.py) checks T3, starts a
userspace daemon in the background, guides browser login, and configures
private HTTPS access. It prints the complete device hostname and a command
to test from your desktop. After you confirm that the desktop reaches the
same T3 environment, it generates a pairing token and shows the desktop
connection and provider binary settings. Login links and tokens appear only
in your interactive terminal; the script does not save them to a log.

Rerun the command after a container restart. It reuses the saved login and
matching Serve configuration. It refuses to overwrite a different service
on port 443 or continue with public Funnel access enabled. If you stop before
pairing, the daemon and completed Tailscale setup remain available. A daemon
started by this script survives closing the terminal; a reused, manually
started daemon keeps its original lifecycle.

For a local check without login prompts, configuration changes, or tokens:

```bash
neurodesktop-t3-setup --check
```

This checks local configuration, not reachability from the desktop. The
wizard uses `NEURODESKTOP_T3_CODE_PORT` and `NEURODESKTOP_T3_CODE_HOME` when
set, with defaults `3773` and `~/.t3`. Use `--port` and `--base-dir` if the
terminal does not inherit the server's settings. Tailscale state defaults to
`~/.local/state/tailscale`, and the socket to
`/tmp/tailscale-$(id -u)/tailscaled.sock`. Use `--state-dir` and `--socket` for
a separate instance. Keep state on persistent storage and give concurrent
instances separate state directories. The wizard leaves T3 startup to the
Jupyter extension; if T3 is disabled, enable it in the deployment and restart
Neurodesktop first.

### Manual setup

To run the same steps yourself, start the daemon in the foreground:

```bash
install -d -m 0700 "$HOME/.local/state/tailscale" "/tmp/tailscale-$(id -u)"
tailscaled --tun=userspace-networking --port=0 \
  --statedir="$HOME/.local/state/tailscale" \
  --state="$HOME/.local/state/tailscale/tailscaled.state" \
  --socket="/tmp/tailscale-$(id -u)/tailscaled.sock"
```

Keep that terminal running. In a second terminal, authenticate and configure
the private HTTPS proxy:

```bash
tailscale --socket="/tmp/tailscale-$(id -u)/tailscaled.sock" up --accept-dns=false
tailscale --socket="/tmp/tailscale-$(id -u)/tailscaled.sock" serve --bg http://127.0.0.1:3773
t3 pair
```

Follow the login link from `up`. If prompted by `serve`, enable HTTPS for the
tailnet. In the desktop app, enter the `https://…ts.net` host printed by
`serve` and the token printed by `t3 pair`. The ordinary pairing URL still
uses the container's address, so use the separate host and token fields.
Tailscale Serve is private to the tailnet; no public ingress is required.
See [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve).

This manual setup uses an explicit user-owned socket. `t3 pair --tailscale`
expects to invoke the Tailscale CLI itself, so the commands above configure
Serve directly instead. On container restart, start the daemon again with the
same state directory. Keep that directory on the persistent home volume and
give each Neurodesktop instance its own Tailscale state directory.

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

T3 reloads the login-shell `PATH` at startup, which can put the interactive
Codex wrapper before the quiet directory. When `T3CODE_HOME` is set, that
wrapper sends `codex app-server` straight to the quiet image launcher before
printing banners, rewriting config, or injecting CLI flags. This keeps the
app-server stdout stream valid JSON.

On an older image, a `decode-wire-message` provider-probe error can mean an
interactive banner entered that stream. In the remote environment's Codex
provider settings, set **Binary path** to
`/opt/neurodesktop/t3-provider-bin/codex`, then refresh the provider status.

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
