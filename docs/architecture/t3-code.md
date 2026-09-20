---
title: T3 Code remote access
description: T3 Code in JupyterLab, server lifecycle, provider paths, persistent
  state, and desktop connection procedures
parent: ../architecture.md
status: current
last-reviewed: "2026-09-19"
---

# T3 Code remote access

Part of [Architecture](../architecture.md). The image includes a pinned T3 Code
server. A Jupyter Server extension starts it automatically as the notebook user.

## Open inside JupyterLab

Choose **scigent.ai** in the JupyterLab launcher's **Neurodesk** section. It opens the installed web
application in a main-panel tab. Reopening the launcher focuses the existing
tab. Closing the tab leaves the Jupyter-owned T3 process running.

The launcher connects using your existing Jupyter login. It establishes T3's
browser session automatically; no terminal command or pairing token is needed.
The one-time credential stays on the server, and the browser receives only an
HttpOnly session cookie scoped to the T3 route. Existing sessions are reused.
Provider authentication still belongs to the container, as described
[below](#providers).

This route requires only the Jupyter endpoint, including its existing HTTPS
and JupyterHub user prefix. It does not require Tailscale or a separately
published T3 port. Leave `NEURODESKTOP_T3_CODE_HOST` at its loopback default
when using only JupyterLab. The launcher reports when the sidecar is not ready.

The server extension authenticates `/neurodesk-t3/` and proxies HTTP and
WebSockets to its supervised process. It strips Jupyter credentials before
forwarding requests and scopes T3 session cookies to that route. The pinned
T3 client assumes root-relative URLs, so the proxy adapts its router, asset
loader and file URLs, with a transport adapter running only inside the T3
frame. Upstream anchor changes fail explicitly. Adapted assets are not cached
as immutable files. Desktop access continues to use the unmodified client.

JupyterHub 6 applies XSRF checks to CORS-mode GET requests, including native
JavaScript module imports. These imports cannot attach the fetch adapter's
XSRF header. The proxy accepts authenticated GET/HEAD requests for static
JS, CSS, WASM bundles and the manifest when the browser supplies
`Sec-Fetch-Site: same-origin`. Other origins, API requests and writes retain
Jupyter's XSRF checks. The manifest also uses `crossorigin="use-credentials"`
so its request carries the Jupyter login cookie. Without this handling, the
entry module loops through Hub login redirects and T3 stays on its splash screen.

## Connect through a published Docker port

Publish T3 only on the Docker host's loopback interface. Use host port 3774
to avoid conflicting with the desktop app's local server on port 3773;
the container server still listens on port 3773:

```bash
docker run \
  -e NEURODESKTOP_T3_CODE_HOST=0.0.0.0 \
  -p 127.0.0.1:8888:8888 \
  -p 127.0.0.1:3774:3773 \
  neurodesktop:latest
```

The repository's [`build_and_run.sh`](../../build_and_run.sh) uses these T3
settings in its normal interactive mode.

After Neurodesktop starts, open a JupyterLab terminal and mint a one-time
pairing link for the address your desktop reaches:

```bash
t3 auth pairing create --ttl 5m --base-url http://127.0.0.1:3774
```

In the T3 Code desktop app, open **Settings → Connections → Add environment**
and paste the printed `Pair URL` into the **Host** field; it fills in the host
and the pairing code. Each new desktop device needs a fresh link. `t3 pair`
mints the same kind of token, but builds its URL and QR code from the
container's own address, which the desktop cannot reach.

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

T3 starts automatically with Jupyter. Its default host, `127.0.0.1`, works
for this setup. In a JupyterLab terminal,
run the guided setup as the notebook user:

```bash
t3_neurodesk_setup
```

The [setup script](../../scripts/t3_neurodesk_setup.py) checks T3, starts a
userspace daemon in the background, guides browser login, and configures
private HTTPS access. It prints the private `https://…ts.net` address and a
command to test from your desktop. After you confirm that the desktop reaches
the same T3 environment, it prints one pairing link to paste into the **Host**
field of **Settings → Connections → Add environment**, which fills in the host
and the pairing code. It builds that link with `t3 auth pairing create
--base-url`, not `t3 pair`, because `t3 pair` builds its URL and QR code from
the container's own address, which no desktop can reach. Login links and
pairing links appear only in your interactive terminal; the script does not
save them to a log.

Rerun the command after a container restart. It reuses the saved login and
matching Serve configuration.

The container's hostname is its container ID, so a recreated container would
join the tailnet under a new name and every address printed by an earlier
setup would stop resolving. Setup therefore names the device `neurodesktop`
once, which keeps its address stable across restarts. Use
`--tailscale-hostname` for a different name, or pass an empty value to keep
the name Tailscale chooses. Tailscale appends a suffix when the name is
already taken in the tailnet; that assigned name is stable too, so setup
keeps it.

Tailscale answers only on the name a device holds now, so a Serve endpoint
left under an earlier name resolves nowhere. Setup reports such an endpoint as
superseded and configures the current name instead of offering an address the
desktop cannot reach. It leaves the old entry in place; clear it with
`tailscale serve reset` when nothing else uses Serve. Setup refuses to
overwrite a different service on port 443 or continue with public Funnel
access enabled. If you stop before
pairing, the daemon and completed Tailscale setup remain available. A daemon
started by this script survives closing the terminal; a reused, manually
started daemon keeps its original lifecycle. To disconnect this device from
the tailnet, run
`tailscale --socket=/tmp/tailscale-$(id -u)/tailscaled.sock down`.

For a local check without login prompts, configuration changes, or tokens:

```bash
t3_neurodesk_setup --check
```

This checks local configuration, not reachability from the desktop. The
wizard uses `NEURODESKTOP_T3_CODE_PORT` and `NEURODESKTOP_T3_CODE_HOME` when
set, with defaults `3773` and `~/.t3`. Use `--port` and `--base-dir` if the
terminal does not inherit the server's settings. Tailscale state defaults to
`~/.local/state/tailscale`, and the socket to
`/tmp/tailscale-$(id -u)/tailscaled.sock`. Use `--state-dir` and `--socket` for
a separate instance. Keep state on persistent storage and give concurrent
instances separate state directories. The wizard leaves T3 startup to the
Jupyter extension. If T3 is unavailable after Jupyter starts, check the
Jupyter server log before rerunning setup.

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
tailscale --socket="/tmp/tailscale-$(id -u)/tailscaled.sock" up \
  --accept-dns=false --hostname=neurodesktop
tailscale --socket="/tmp/tailscale-$(id -u)/tailscaled.sock" serve --bg http://127.0.0.1:3773
t3 auth pairing create --ttl 5m --base-url https://<name>.ts.net
```

Follow the login link from `up`. If prompted by `serve`, enable HTTPS for the
tailnet. Use the `https://…ts.net` name that `serve` printed as `--base-url`,
then paste the printed `Pair URL` into the **Host** field of
**Add environment**.
Tailscale Serve is private to the tailnet; no public ingress is required.
See [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve).

This manual setup uses an explicit user-owned socket. `t3 pair --tailscale`
expects to invoke the Tailscale CLI itself, so the commands above configure
Serve directly instead. On container restart, start the daemon again with the
same state directory. Keep that directory on the persistent home volume and
give each Neurodesktop instance its own Tailscale state directory.

## Connect through T3 Connect

T3 Connect uses an outbound managed connection, so it does not need a
published port. Keep the sidecar on its default loopback host. In a
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
For the standalone desktop app, users create pairing tokens explicitly with
`t3 pair`. The JupyterLab launcher uses an authenticated, XSRF-protected POST
to `/neurodesk-t3/_session`. The server creates a one-minute credential using
T3's CLI and immediately exchanges it for a browser session on the supervised
server. Neither the credential nor upstream error bodies are returned to the
frontend or written to logs. A failed exchange leaves a short-lived credential
that expires automatically.

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

T3 0.0.42 publishes one self-contained executable per platform, with its web
client, its `node-pty` build and its other native modules bundled beside it.
The Dockerfile installs the manifest under `config/agents/t3-code` into
`/opt/t3-code`, so the layer compiles nothing, runs no install script, and
keeps exactly one `@t3code/t3-linux-<arch>` build. The executable links
against `libatomic1`, which the layer installs; its build-time version check
fails if that library is missing.

This packaging replaces the earlier locked npm tree. `npm ci` cannot install
it: npm records only the bundled tree of the platform that generated the
lockfile, then refuses the install everywhere else and silently drops the
optional platform package on the other architecture. The exact pins carry the
same guarantee instead — the manifest pins `t3`, `t3` pins each
`@t3code/t3-<platform>` build, and each build carries its dependencies inside
its own tarball.

Both amd64 and arm64 images must pass the installed server and PTY checks.
0.0.42 ships a resource monitor for both Linux architectures, so arm64 no
longer omits resource telemetry; server, terminal, pairing, and provider
behavior must still pass.
