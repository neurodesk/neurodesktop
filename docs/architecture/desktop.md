---
title: Desktop environment
description: LXDE desktop over VNC/RDP through Guacamole, clipboard sync,
  per-display Firefox profiles, and office file associations
parent: ../architecture.md
status: current
last-reviewed: "2026-09-20"
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
