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

Webapps are defined in `webapps.json`, which is fetched from the neurocommand
repository. [`scripts/generate_jupyter_config.py`](../scripts/generate_jupyter_config.py)
generates Jupyter Server Proxy entries. Each webapp launches through
[`config/jupyter/webapp_launcher.sh`](../config/jupyter/webapp_launcher.sh) and
uses Unix sockets such as `/tmp/neurodesk_webapp_{name}.sock` to avoid port
conflicts.

### Desktop Environment

The desktop environment uses LXDE with TigerVNC for VNC access. Apache Guacamole
provides browser-based remote desktop access. Configuration lives in
[`config/lxde/`](../config/lxde/) and [`config/guacamole/`](../config/guacamole/).

### Services

- JupyterLab: main interface on port 8888
- code-server: VS Code in JupyterLab, with default extensions installed from
  [`config/jupyter/jupyterlab_startup.sh`](../config/jupyter/jupyterlab_startup.sh)
- Apache Tomcat: serves the Guacamole web application
- VNC: desktop access through Guacamole
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

CI includes multi-architecture builds for amd64 and arm64.

## Build-Time Behaviors

### Config Generation

The Dockerfile fetches `webapps.json` from the neurocommand repository and
generates `jupyter_notebook_config.py` using a template system. To add new
webapps, update the source `webapps.json`.

### User Permissions

The container runs as the `jovyan` user from the base Jupyter image. The
`NB_UID` and `NB_GID` environment variables allow matching host user
permissions.

### CVMFS Setup

CVMFS configuration files exist for different regions and modes, including
direct and CDN access. The startup script probes servers and copies the best
configuration to the active location.
