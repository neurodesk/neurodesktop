---
title: Desktop environment
description: LXDE desktop over VNC/RDP through Guacamole, clipboard sync,
  per-display Firefox profiles, and office file associations
parent: ../architecture.md
status: current
last-reviewed: "2026-07-31"
---

# Desktop Environment

Part of [Architecture](../architecture.md). Related environment variables are
listed in
[Environment variables](../environment-variables.md#desktop-vncrdp-guacamole-firefox).

The desktop environment uses LXDE with TigerVNC for VNC access and xrdp for RDP
access. Apache Guacamole provides browser-based remote desktop access. JupyterLab
exposes separate `Neurodesktop RDP` and `Neurodesktop VNC` launcher entries so
opening one backend does not start the other. In unprivileged Apptainer or
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
