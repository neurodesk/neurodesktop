---
title: Desktop environment
description: LXDE desktop over VNC/RDP through Guacamole, voxl desktop
  streaming with a patched Xvfb, clipboard sync, per-display Firefox
  profiles, and office file associations
parent: ../architecture.md
status: current
last-reviewed: "2026-11-10"
---

# Desktop Environment

Part of [Architecture](../architecture.md). Related environment variables are
listed in
[Environment variables](../environment-variables.md#desktop-vncrdp-guacamole-firefox).

The desktop environment uses LXDE with TigerVNC for VNC access and xrdp for RDP
access. Apache Guacamole provides browser-based remote desktop access. JupyterLab
exposes separate `Neurodesktop RDP` and `Neurodesktop VNC` launcher entries so
opening VNC does not create an RDP desktop session. Root startup initializes
the xrdp listener before Jupyter drops privileges. In unprivileged Apptainer or
Singularity sessions, the RDP launcher entry is hidden because starting or
reconfiguring xrdp requires root/sudo permissions; the VNC launcher remains
available. Configuration lives in
[`config/lxde/`](../../config/lxde/) and [`config/guacamole/`](../../config/guacamole/).
The image installs the pre-rendered `config/lxde/background.png` wallpaper;
`config/lxde/background.svg` is its editable source and does not ship in the
runtime image.
The RDP and VNC proxy entries use backend-specific Guacamole state directories
under `~/.neurodesk` (`guacamole-*`, `tomcat-*`, and `runtime-*`) so one backend
does not reuse the other backend's cached connection mapping. Firefox launches
through `/usr/local/bin/neurodesktop-firefox`, which assigns a Firefox profile
for each X display and lets Firefox register that profile in its standard
profile store. If Firefox's profile-creation command does not write the profile
metadata, the wrapper creates the profile directory and `profiles.ini` entry
itself. Simultaneous VNC and RDP desktops therefore do not contend for the same
default Firefox profile.

The image puts a small `Xtigervnc` launcher beside a link to the distribution's
`tigervncserver` command in `/usr/local/bin`. It confines only the virtual X
server child to Mesa's EGL vendor so NVIDIA Container Toolkit driver injection
cannot make it load an incompatible host NVIDIA EGL library. The parent launcher
and LXDE applications retain the deployment's normal EGL vendor selection.

Before invoking `vncserver`, Guacamole startup creates the TigerVNC config
parent: an absolute `XDG_CONFIG_HOME`, or `~/.config` otherwise. TigerVNC then
migrates the legacy `~/.vnc` directory itself and leaves a compatibility
symlink. Startup does not pre-create the `tigervnc` leaf, which would bypass
that migration and leave saved configuration and credentials behind.

## GPU rendering prerequisites

The image installs checksum-pinned VirtualGL 3.1.5 packages on amd64 and
arm64, plus Mesa's graphics diagnostics. `vglrun` is available on `PATH`;
`vgl-eglinfo` selects VirtualGL's EGL diagnostic rather than Mesa's similarly
named tool. The image requests NVIDIA runtime capabilities
`compute,utility,graphics,display` by default. Operators still provide the
host driver, NVIDIA Container Toolkit integration, and an assigned GPU.
An explicit deployment capability setting overrides the image default.

The existing LXDE, TigerVNC, and Guacamole services retain their launch paths.
Applications use VirtualGL only when explicitly launched through it. From a
desktop terminal, run:

```bash
neurodesktop-gpu-check egl0
vglrun -d egl0 -c proxy YOUR_IMAGE_LOCAL_OPENGL_APPLICATION
```

The diagnostic runs EGL initialization and a GLX rendering probe in the
current `DISPLAY`, prints the renderer, and fails unless it reports NVIDIA's
OpenGL vendor. Its optional device argument overrides `VGL_DISPLAY`, which
otherwise defaults to `egl0`. Select the device after checking the allocation
inside the container; an EGL index is not a scheduler GPU UUID.

These are image prerequisites, not automatic acceleration for scientific tools.
FreeView runs in a nested Apptainer image whose `--cleanenv` boundary needs
explicit integration. The [tool integration checklist](../designs/gpu-tool-integration.md)
lists the required FreeSurfer and Neurocommand changes. Run the hardware
[acceptance profile](../testing.md#virtualgl-and-gpu-rendering) before claiming
GPU rendering support. NVIDIA/amd64 is the initial target; arm64 hardware,
RDP rendering, and Kasm platform GPU sessions remain unverified.

## Credentials and service access

The image locks the notebook account's password at build time. Root startup
generates a random RDP password and stores it under
`/var/lib/neurodesktop/rdp/`, readable only by root. It reuses that password on
container restart and supplies a mode-0600 copy to the notebook UID under
`/run/neurodesktop/rdp/`. Recreating the container generates a new credential.
The RDP mapping reads that credential rather than a shared default. Startup
reapplies the managed password, so manual OS password changes are not persistent.
An unprivileged startup does not modify the host account and does not advertise
an RDP connection without provisioned credentials.

xrdp listens on `127.0.0.1`, at port 3389 unless the operator specifies another
port. Its root service starts during initialization so the notebook user does
not need sudo access to service management. Guacamole and the desktop session
still start when their launcher opens. Local Slurm and CVMFS retain their root
startup worker. Guacamole web and VNC credentials remain separate per-user
secrets. See [startup privileges](../environment-variables.md#startup-privileges)
for the package-only sudo policy.

Guacamole's Tomcat HTTP connector also listens only on `127.0.0.1`. Each
launcher configures its per-user port and loopback address together, disables
Tomcat's shutdown TCP port, and stops before launch if the copied `server.xml`
does not contain exactly one active HTTP/1.1 connector. Browser access therefore
continues through Jupyter's authenticated proxy without exposing the Guacamole
login surface to container peers or users sharing an HPC network namespace.

The packaged xrdp daemon runs as `xrdp`. The image sets sesman's
`SessionSockdirGroup=xrdp` so that daemon can reach the notebook user's Xorg
and channel-server sockets. Their parent directory excludes other users, and
the sockets remain mode `0660`. Leaving the group at `root` prevents xrdp from
forwarding the desktop even after authentication succeeds.

The SSH daemon used only for Guacamole's VNC SFTP side-channel listens on
`127.0.0.1`. Startup checks every effective listener address, including
addresses from included files. A saved configuration that allows another
address uses a minimal loopback-only fallback without replacing the saved
file. This prevents persisted configurations from exposing the service on a
container or shared HPC network interface. On shared-network runtimes, an
automatically selected port is retried when another session claims it between
the availability probe and sshd's bind; a caller-specified port remains
strict. Its PID file and per-port mode-0600 daemon log stay in the user's
mode-0700 SSH directory, so another UID on a shared host cannot replace the
daemon's state path between startup checks and the daemon opening it. The log
persists because OpenSSH reopens it on each connection; it is truncated on the
next launch for that port. When sshd starts through sudo, readiness reads its root-owned mode-0600
PID file through sudo too. Startup checks that the live daemon PID owns the
local listener rather than trusting a saved PID, which can be reused after a
container restart or belong to another host sharing the home directory. It
uses the process annotation reported by `ss` when available, and otherwise
matches the listener's kernel socket inode to the daemon's file descriptors;
an unrelated process on the selected port is never accepted. After launching
sshd, startup polls for that owned listener for up to five seconds before
publishing the SFTP port: the daemon parent can exit before its child binds. If
the listener never appears, startup reports the SSH diagnostic and leaves SFTP
disabled. Startup
also forces every authenticated session through OpenSSH's in-process SFTP
server and disables TCP, Unix-socket, agent, and X11 forwarding plus TTY
allocation. These command-line restrictions apply even when an older home
directory restores a configuration that previously allowed an interactive
shell or forwarding.

Set `NEURODESKTOP_DISABLE_SSH=true` before startup to disable that daemon and
the separate `jupyter-sshd-proxy` endpoint. Guacamole startup removes stale SFTP
port state and disables its SFTP connection before Tomcat loads the mapping,
so the VNC desktop still connects without SFTP transfers. The Jupyter server
configuration filters the installed `sshd` proxy entrypoint before it loads,
preserving other proxy services. The Sherlock launcher sets this variable in
its Apptainer invocation. Its tunnel uses the cluster's host SSH servers and
does not require an SSH server inside the image.
The Sherlock launcher also binds Jupyter to `127.0.0.1`; its SSH tunnel
connects to that loopback listener on the compute node.

The Neurodesk menu's **Update** entry updates the notebook-owned
`/neurocommand` checkout without sudo. Root startup repairs checkout ownership
after notebook UID or GID changes, without executing its scripts. See
[Neurocommand](cvmfs.md#neurocommand) for update behavior and HPC restrictions.

Failure to start the optional xrdp service leaves Jupyter and VNC available.
Failure to provision credentials or validate sudo policy stops startup.
Root startup also prepares the ARM CPU-information workaround used by MATLAB,
without granting the notebook user mount privileges. The workaround remains
best-effort on runtimes that deny bind mounts.

VS Code uses a mode-0600 Unix socket in a private temporary directory allocated
by Jupyter Server Proxy. It does not open an unauthenticated TCP listener on the
shared host. Jupyter authenticates browser requests before forwarding them.
The proxy's [Unix-socket option](https://jupyter-server-proxy.readthedocs.io/en/latest/server-process.html#unix-socket)
provides the private directory and WebSocket transport.

The proxy considers VS Code ready as soon as its socket returns any HTTP
response. code-server listens before it registers its routes, so the proxy could
forward a user's first request to an empty router and show a 404.
`config/jupyter/code_server_ready.py` starts code-server on a staging socket in
the same private directory and renames it to the proxy's path once `/healthz`
returns 200. The launcher forwards termination signals and gives code-server a
parent-death signal, so the proxy's SIGKILL cannot leave code-server running.
Each launch uses its own staging name and removes the published socket only if
it is still the one that launch placed. An orphan from a launch that missed the
proxy's timeout cannot remove the next launch's socket while it shuts down.

## voxl desktop streaming

The `Neurodesktop voxl` launcher streams an LXDE desktop with
[voxl](https://github.com/tembo/voxl). voxl encodes the X display as H.264
with x264 and sends video, input, and clipboard over one WebSocket. The browser
decodes the stream with WebCodecs. The Guacamole VNC and RDP launchers are
unchanged.

[`config/voxl/voxl.sh`](../../config/voxl/voxl.sh) starts its own Xvfb on the
first free display from `:60` to `:99`. Guacamole probes and kills displays
`:1` to `:42`, so the two do not collide. This desktop is separate from the
Guacamole desktops. Xvfb opens no TCP listener and admits X clients with a
fresh cookie in `~/.Xauthority`. The launcher treats the display as ready only
after Xvfb reports its number through `-displayfd`, so another server already
answering on that display cannot pass for the new one.

Xvfb runs with `-shmem`, so voxl reads the framebuffer in place instead of
copying each frame through the X server. Xvfb creates that SysV segment with
mode `0777`. Apptainer shares the host's IPC namespace, so on a shared HPC node
any user could otherwise read or write the desktop.
`neurodesktop-shm-private`, built from
[`config/voxl/neurodesktop-shm-private.c`](../../config/voxl/neurodesktop-shm-private.c),
sets the segments that Xvfb created to mode `0600`. It then requires Xvfb to
be the only attachment. A process that attached before the mode change keeps
its mapping, so the launcher stops and streams nothing if any other process is
attached. RandR cannot grow an Xvfb screen past its starting size. The screen
therefore starts at 3840x2160, and voxl reduces it to fit the viewer.

The launcher runs `/usr/local/libexec/voxl/Xvfb`, not the distro's
`/usr/bin/Xvfb`. The `voxl-builder` stage rebuilds Ubuntu's `xorg-server`
source, at the version of the `xvfb` package that the runtime installs, with
Debian's configure flags. It adds voxl's
[XTEST scroll-axis patch](../../config/voxl/xorg-server-xtest-scroll.patch),
which gives the XTEST pointer vertical and horizontal scroll axes. voxl then
sends trackpad and high-resolution wheel input as pixel-precise scrolling
instead of whole wheel steps. The runtime `xvfb` package still supplies the
server's libraries and XKB tools, and Glass and `xvfb-run` keep the unpatched
server. A distro security update to `xorg-server` reaches this Xvfb only when
the image is rebuilt.

voxl listens on `127.0.0.1` at the port Jupyter Server Proxy assigns. Each
Jupyter configuration load creates a random token. The proxy passes it to the
launcher as `VOXL_PASSWORD` and adds `Authorization: Bearer <token>` to every
proxied request, including the WebSocket upgrade. Other users on a shared host
can reach the loopback port, but voxl answers them with HTTP 401. The token
never appears on a command line or in the logs. voxl's own login stays
enabled. `--allowed-hosts '*'` accepts the browser's Jupyter host name,
because the token, not the host name, admits a request. The launcher turns off
desktop audio and voxl's file transfer panel. When Jupyter Server Proxy stops
the launcher, the launcher stops voxl, then the desktop session, then Xvfb,
and removes the display's cookie.

The launcher is hidden in Glass and when `/usr/local/bin/voxl` is missing.
Limits:

- WebCodecs requires HTTPS or `localhost`. A plain-HTTP remote address shows
  no video; use a Guacamole launcher there.
- Encoding is CPU-only. The image has no PulseAudio, so there is no desktop
  audio or microphone.
- The stage builds `voxl-server` from crates.io with Ubuntu's Rust toolchain.
  The version is set by
  [`VOXL_VERSION`](../environment-variables.md#build-arguments). The x264 it
  links makes the binary GPL-2.0.

## Clipboard sync

Clipboard sync between the browser and the remote desktop uses Guacamole's
stock focus-driven `navigator.clipboard` integration in Chrome-family browsers.
Safari and Firefox restrict clipboard reads outside an explicit paste gesture
(Safari has no persistable clipboard-read permission at all), and no browser
makes Cmd+V paste into the remote session, so the Dockerfile injects
[`config/guacamole/mac-clipboard-shim.js`](../../config/guacamole/mac-clipboard-shim.js)
into the Guacamole webapp's `index.html`. On macOS (any browser) the shim
intercepts Cmd+V, lets the browser's paste command land in a hidden textarea
and reads the text from the paste event's `clipboardData` (prompt-free in
every engine, unlike `navigator.clipboard.readText()`), streams it to the
remote clipboard through Guacamole's `clipboardService`, and synthesizes
Shift+Insert in the remote session (pastes in both terminals and GUI apps);
text copied in the remote session is cached and flushed to the local clipboard
on the next user gesture (Cmd+C or a mouse click). The shim is a no-op on
non-macOS platforms, and its `index.html` script tag carries a content-hash
query so browser caches cannot serve a stale shim after an image upgrade.
Because Guacamole's RDP clipboard channel only populates the X11 CLIPBOARD
selection while VTE terminals paste PRIMARY on Shift+Insert, xrdp sessions
also start `autocutsel` (via
[`config/lxde/75neurodesk-clipboard-sync`](../../config/lxde/75neurodesk-clipboard-sync)
in `/etc/X11/Xsession.d/`) to bridge the two selections; VNC sessions already
get this from TigerVNC's `vncconfig`.

## File associations

Double-clicking a file in the desktop resolves its MIME type through the
default-user [`config/lxde/mimeapps.list`](../../config/lxde/mimeapps.list).
Office documents (.odt, .docx, .xlsx, .pptx, ...) open in the Neurodesk
LibreOffice container apps: at image build time,
[`config/lxde/update_office_mimeapps.py`](../../config/lxde/update_office_mimeapps.py)
reads the `MimeType=` declarations from the neurocommand-generated LibreOffice
`.desktop` entries, registers the newest version as the default handler for
each declared type, and removes xarchiver's claim on them (ODF/OOXML documents
are zip containers, so the archive manager would otherwise win). The build
fails if the neurocommand revision in the image does not declare MIME types in
its menu entries yet.
