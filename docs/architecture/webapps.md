---
title: Webapp system
description: Container-backed and hosted webapps, launcher tiles and icons,
  and the build-time Jupyter config generation that wires them up
parent: ../architecture.md
status: current
last-reviewed: "2026-08-07"
---

# Webapp System

Part of [Architecture](../architecture.md). Related environment variables are
listed in
[Environment variables](../environment-variables.md#container-backed-webapps).

Container-backed webapps are defined in `webapps.json`, which is fetched from
the neurocommand repository. Hosted webapp links and local overrides are defined
in [`config/jupyter/webapp_links.json`](../../config/jupyter/webapp_links.json) and
applied by [`scripts/generate_jupyter_config.py`](../../scripts/generate_jupyter_config.py)
when generating Jupyter Server Proxy entries. The same merged webapp config is
written to `/opt/neurodesktop/webapps.json` so runtime wrapper settings such as
path rewrites use the local overrides too. The wrapper streams fixed-length
request bodies to the backend in bounded chunks, so large uploads are not
duplicated in wrapper memory; Jupyter Server and the hosting proxy still apply
their own request-size and multipart limits before the wrapper receives a
request. Container-backed webapps launch through
[`config/jupyter/webapp_launcher.sh`](../../config/jupyter/webapp_launcher.sh) and
use Unix sockets such as `/tmp/neurodesk_webapp_{name}.sock` to avoid port
conflicts. Entries with `direct_url` open the hosted application directly from
the Neurodesk launcher. Launcher tile icons for those entries are checked-in
SVG or PNG files in
[`config/jupyter/webapp_icons/`](../../config/jupyter/webapp_icons/) referenced from
`webapp_links.json` with `/opt/neurodesk/icons/*` paths; the Dockerfile copies
them into the image before Jupyter config generation. The custom Neurodesk
launcher reads icons through the server-proxy icon endpoint and wraps raster
images as SVGs for JupyterLab `LabIcon` support.

Jupyter Server Proxy buffers ordinary webapp responses through Tornado's HTTP
client. Neurodesktop raises Tornado's matching `max_buffer_size` and
`max_body_size` defaults from 100 MiB to 1024 MiB in
[`jupyter_server_config_extra.py`](../../config/jupyter/jupyter_server_config_extra.py),
so large binary responses such as ezBIDS ZIP exports can complete. Since
container-backed webapps use Unix sockets, an anchored build-time patch in
[`patch_jupyter_server_proxy.py`](../../config/jupyter/patch_jupyter_server_proxy.py)
routes that branch through the configured `AsyncHTTPClient` factory while
preserving its `UnixResolver`; constructing `SimpleAsyncHTTPClient` directly
would retain Tornado's 100 MiB default. This is a per-response ceiling, not
reserved memory: each concurrent buffered download can make the single-user
Jupyter process consume up to the response size, so the limit must remain
finite and deployment memory limits must account for concurrent large
downloads.

## Build-time config generation

The Dockerfile clones neurocommand, copies its `neurodesk/webapps.json`, applies
[`config/jupyter/webapp_links.json`](../../config/jupyter/webapp_links.json), and
generates `jupyter_notebook_config.py` using a template system. It also writes
the merged webapp configuration back to `/opt/neurodesktop/webapps.json`, which
is what the webapp wrapper reads at launch time. To add new container-backed
webapps, update the source `webapps.json`. To add hosted links or make an
existing launcher tile open a hosted app directly, update `webapp_links.json`.
This config generation runs after the neurocommand install layer so local
launcher-link edits do not invalidate the earlier runtime setup layers.
Cached CI builds pass `NEUROCOMMAND_REF` as a resolved neurocommand `main` SHA
so that neurocommand changes invalidate the install layer without requiring
BuildKit to make unauthenticated GitHub API requests from inside the Dockerfile.
The Dockerfile resets the local neurocommand `main` branch to that ref and keeps
it tracking `origin/main` so the runtime Update launcher can use
`git pull --rebase --autostash`.
