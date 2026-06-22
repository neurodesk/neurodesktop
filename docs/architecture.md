# Architecture

## Container Initialization Flow

The startup sequence follows this order:

1. [`config/jupyter/start_notebook.sh`](../config/jupyter/start_notebook.sh)
   sets ownership permissions for the home directory.
2. [`config/jupyter/before_notebook.sh`](../config/jupyter/before_notebook.sh)
   mounts CVMFS, selects the fastest regional server, and configures the
   environment.
3. `jupyter_notebook_config.py` is generated and defines JupyterLab server
   proxies for webapps. It also installs
   [`config/jupyter/jupyterlmod_modulepath.py`](../config/jupyter/jupyterlmod_modulepath.py)
   so the jupyter-lmod side panel refreshes the Jupyter server process
   `MODULEPATH` after lazy CVMFS startup.
4. [`config/jupyter/jupyterlab_startup.sh`](../config/jupyter/jupyterlab_startup.sh)
   starts JupyterLab and associated services.

## Core Components

### CVMFS

CVMFS, the CernVM File System, distributes neuroimaging software containers
without local storage. Regional server selection is based on latency probing for
Europe, America, and Asia. Direct access or CDN mode is selected automatically.

Configuration lives in [`config/cvmfs/`](../config/cvmfs/). CVMFS can be
disabled with `CVMFS_DISABLE=true`.

### Neurocommand

Neurocommand is cloned from
[`neurodesk/neurocommand`](https://github.com/neurodesk/neurocommand) during the
build. It provides the CLI and module system for neuroimaging tools, uses Lmod
for module management, and stores containers in
`/neurodesktop-storage/containers`.

### Webapp System

Container-backed webapps are defined in `webapps.json`, which is fetched from
the neurocommand repository. Hosted webapp links and local overrides are defined
in [`config/jupyter/webapp_links.json`](../config/jupyter/webapp_links.json) and
applied by [`scripts/generate_jupyter_config.py`](../scripts/generate_jupyter_config.py)
when generating Jupyter Server Proxy entries. The same merged webapp config is
written to `/opt/neurodesktop/webapps.json` so runtime wrapper settings such as
path rewrites use the local overrides too. Container-backed webapps launch
through [`config/jupyter/webapp_launcher.sh`](../config/jupyter/webapp_launcher.sh)
and use Unix sockets such as `/tmp/neurodesk_webapp_{name}.sock` to avoid port
conflicts. Entries with `direct_url` open the hosted application directly from
the Neurodesk launcher. Launcher tile icons for those entries are checked-in
SVG or PNG files in
[`config/jupyter/webapp_icons/`](../config/jupyter/webapp_icons/) referenced from
`webapp_links.json` with `/opt/neurodesk/icons/*` paths; the Dockerfile copies
them into the image before Jupyter config generation. The custom Neurodesk
launcher reads icons through the server-proxy icon endpoint and wraps raster
images as SVGs for JupyterLab `LabIcon` support.

### Desktop Environment

The desktop environment uses LXDE with TigerVNC for VNC access and xrdp for RDP
access. Apache Guacamole provides browser-based remote desktop access. JupyterLab
exposes separate `Neurodesktop RDP` and `Neurodesktop VNC` launcher entries so
opening one backend does not start the other. Configuration lives in
[`config/lxde/`](../config/lxde/) and [`config/guacamole/`](../config/guacamole/).
The RDP and VNC proxy entries use backend-specific Guacamole state directories
under `~/.neurodesk` (`guacamole-*`, `tomcat-*`, and `runtime-*`) so one backend
does not reuse the other backend's cached connection mapping. Firefox launches
through `/usr/local/bin/neurodesktop-firefox`, which assigns a Firefox profile
for each X display and lets Firefox register that profile in its standard
profile store. If Firefox's profile-creation command does not write the profile
metadata, the wrapper creates the profile directory and `profiles.ini` entry
itself. Simultaneous VNC and RDP desktops therefore do not contend for the same
default Firefox profile.

### Services

- JupyterLab: main interface on port 8888
- code-server: VS Code in JupyterLab, with default extensions installed from
  [`config/jupyter/jupyterlab_startup.sh`](../config/jupyter/jupyterlab_startup.sh),
  including Python, Jupyter notebook, CSV table editing, NIfTI viewing, GitHub,
  Slurm, and assistant tooling
- Apache Tomcat: serves the Guacamole web application
- RDP and VNC: desktop access through Guacamole, started on demand by the
  selected launcher entry
- SSH: optional SSH server proxy
- Ollama: optional local LLM service when `START_LOCAL_LLMS=1`

## Directory Structure

- [`config/`](../config/): service configurations
- [`config/jupyter/`](../config/jupyter/): JupyterLab config, startup scripts,
  and webapp infrastructure
- [`config/guacamole/`](../config/guacamole/): remote desktop gateway config
- [`config/cvmfs/`](../config/cvmfs/): CVMFS mount configurations and keys
- [`config/lxde/`](../config/lxde/): desktop environment customization
- [`config/firefox/`](../config/firefox/), [`config/vscode/`](../config/vscode/),
  and [`config/itksnap/`](../config/itksnap/): application-specific configs
- [`scripts/`](../scripts/): build-time utilities
- [`.github/workflows/`](../.github/workflows/): CI/CD pipelines
- [`.github/workflows/build-neurodesktop.yml`](../.github/workflows/build-neurodesktop.yml):
  daily automated builds at 17:00 UTC
- [`.github/workflows/test-cvmfs.yml`](../.github/workflows/test-cvmfs.yml):
  CVMFS server health checks

CI includes multi-architecture builds for amd64 and arm64. Registry-sensitive
build paths use local composite actions under
[`.github/actions/`](../.github/actions/) so transient registry transport
failures are retried at login, manifest-check, and registry-copy boundaries
without turning registry timeouts into false cache misses.

## Build-Time Behaviors

### Config Generation

The Dockerfile clones neurocommand, copies its `neurodesk/webapps.json`, applies
[`config/jupyter/webapp_links.json`](../config/jupyter/webapp_links.json), and
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

### Apptainer

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

### User Permissions

The container runs as the `jovyan` user from the base Jupyter image. The
`NB_UID` and `NB_GID` environment variables allow matching host user
permissions.

### CVMFS Setup

CVMFS configuration files exist for different regions and modes, including
direct and CDN access. The startup script probes servers and copies the best
configuration to the active location.
