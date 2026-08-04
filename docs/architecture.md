---
title: Architecture
description: Startup flow, services, directory layout, and the map of
  per-subsystem architecture pages
parent: index.md
status: current
last-reviewed: "2026-08-04"
---

# Architecture

This is the hub page: it covers the container startup flow, the running
services, and the repository layout, and links to one page per subsystem
under [`docs/architecture/`](architecture/). The wiki entry point is
[the docs index](index.md).

## Subsystem pages

| Page | Covers |
| --- | --- |
| [CVMFS and Neurocommand](architecture/cvmfs.md) | CVMFS server selection, mount configuration, and the neuroimaging tool/module system |
| [Desktop environment](architecture/desktop.md) | LXDE over VNC/RDP through Guacamole, clipboard sync, Firefox profiles, office file associations |
| [Webapp system](architecture/webapps.md) | Container-backed and hosted webapp tiles, and the build-time Jupyter config generation |
| [Workspace link routing](architecture/workspace-link-routing.md) | Opening agent-authored absolute file links inside JupyterLab |
| [ASTRA integration](architecture/astra.md) | `astra`/`lc` CLIs, the shared agent skill, and the provenance viewer |
| [Coding agents](architecture/coding-agents.md) | Claude Code, the OpenCode terminal wrapper, and session pruning |
| [Jupyter AI](architecture/jupyter-ai.md) | ACP personas, chat workspace seeding, collaboration-stack workarounds |
| [Agentic CI workflows](architecture/agentic-workflows.md) | Issue investigation and the weekly maintenance suite |
| [Build-time behaviors](architecture/build.md) | Notebook Intelligence and MyST/RISE rebuilds, Apptainer build stage, user permissions |

## Container Initialization Flow

The startup sequence follows this order (per
[`config/jupyter/startup_order.md`](../config/jupyter/startup_order.md)):

1. [`config/jupyter/start_notebook.sh`](../config/jupyter/start_notebook.sh)
   sets ownership permissions for the home directory.
2. [`config/jupyter/before_notebook.sh`](../config/jupyter/before_notebook.sh)
   mounts CVMFS, ranks the CVMFS servers by measured download throughput via
   [`config/jupyter/cvmfs_server_select.sh`](../config/jupyter/cvmfs_server_select.sh),
   and configures the environment. It also launches
   [`config/jupyter/print_access_url.sh`](../config/jupyter/print_access_url.sh)
   in the background, which waits until the Jupyter server answers HTTP and
   then reprints the access URL (read from the server's `jpserver-<pid>.json`
   runtime info file) at the end of the startup log, where the ServerApp's own
   token banner has already scrolled out of view.
3. [`config/jupyter/jupyterlab_startup.sh`](../config/jupyter/jupyterlab_startup.sh)
   starts JupyterLab and associated services. It also runs
   [`opencode_prune_sessions.py`](../config/agents/opencode_prune_sessions.py)
   once per container start (see
   [OpenCode session pruning](architecture/coding-agents.md#opencode-session-pruning)).
4. `jupyter_notebook_config.py` is loaded when the Jupyter ServerApp starts.
   It is generated at image build time (see
   [config generation](architecture/webapps.md#build-time-config-generation))
   and defines JupyterLab server proxies for webapps. It also installs
   [`config/jupyter/jupyterlmod_modulepath.py`](../config/jupyter/jupyterlmod_modulepath.py)
   so the jupyter-lmod side panel refreshes the Jupyter server process
   `MODULEPATH` after lazy CVMFS startup.

## Services

- JupyterLab: main interface on port 8888
- code-server: VS Code in JupyterLab, with default extensions installed from
  [`config/jupyter/jupyterlab_startup.sh`](../config/jupyter/jupyterlab_startup.sh),
  including Python, Jupyter notebook, CSV table editing, NIfTI viewing, GitHub,
  Slurm, and assistant tooling
- Apache Tomcat: serves the Guacamole web application
- RDP and VNC: desktop access through Guacamole, started on demand by the
  selected launcher entry
- SSH: optional SSH server proxy
- Slurm: integrated single-node scheduler (or host-cluster mode); see
  [`config/slurm/README.md`](../config/slurm/README.md)

The AI tools can additionally use an Ollama server reachable via
`OLLAMA_HOST` (by default the Docker host); the image does not bundle Ollama
itself.

## Directory Structure

- [`config/`](../config/): service configurations
- [`config/jupyter/`](../config/jupyter/): JupyterLab config, startup scripts,
  and webapp infrastructure
- [`config/agents/`](../config/agents/): coding-agent wrappers, OpenCode
  session pruning, and Notebook Intelligence setup
- [`config/guacamole/`](../config/guacamole/): remote desktop gateway config
- [`config/cvmfs/`](../config/cvmfs/): CVMFS mount configurations and keys
- [`config/lxde/`](../config/lxde/): desktop environment customization
- [`config/slurm/`](../config/slurm/): integrated Slurm scheduler setup
- [`config/ssh/`](../config/ssh/): SSH/SFTP server setup
- [`config/firefox/`](../config/firefox/), [`config/vscode/`](../config/vscode/),
  and [`config/itksnap/`](../config/itksnap/): application-specific configs
- [`scripts/`](../scripts/): build-time utilities and installed runtime CLIs
- [`extensions/`](../extensions/): in-repo JupyterLab extensions
  (`neurodesk-launcher`, `astra-viewer`)
- [`tests/`](../tests/): the two-tier test suite (see [Testing](testing.md));
  [`tests/fixtures/astra-bet/`](../tests/fixtures/astra-bet/) is the canonical
  worked ASTRA spec installed into the image as an example
- [`docs/`](index.md): this wiki
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

## Apptainer

When `HOME` names an existing directory and
[`APPTAINER_HOME`](environment-variables.md#apptainer) is unset or empty,
`environment_variables.sh` defaults `APPTAINER_HOME` to `HOME`; an explicit
override is preserved. This makes Apptainer use a supplied home path instead
of resolving one from the host uid in `/proc/self/uid_map`. In the rootless
Podman setup reported in issue #804, that uid has no `/etc/passwd` entry inside
the container, leaving the default home empty and causing nested tool
containers to abort with `failed to add  as session directory: path . is not
an absolute path`.
