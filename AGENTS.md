# AGENTS.md

## Important Guidelines

- when fixing a bug always test if the fix worked before reporting that the problem is fixed
- when implementing a new feature, always create a test for the new feature and make sure that the AGENTS.md file is up-to-date.

## Tests

when making changes to the project, make sure you add tests for new functionality, build the container and run the tests inside the container under /opt/tests/ to ensure that your changes do not break existing functionality.

### Negative Test Convention

When adding tests for pipeline or module-loading workflows, always include a **negative test** alongside the positive (happy-path) test. The negative test should use `module load funny-name-tool` (a non-existent module) and assert that the workflow **fails** (non-zero exit code) and does **not** produce output. This guards against silent failures caused by `set +euo pipefail` and `|| true` patterns in workflow scripts.

```bash
pytest /opt/tests/
```

### Building the Container

```bash
# Build the Docker image locally
docker build . -t neurodesktop:latest

# Build and run using the convenience script
./build_and_run.sh
```

The [build_and_run.sh](build_and_run.sh) script builds the image and runs it with recommended settings (persistent home, CVMFS enabled, port 8888).

## High-Level Architecture

### Container Initialization Flow

The startup sequence follows this order:
1. **[config/jupyter/start_notebook.sh](config/jupyter/start_notebook.sh)**: Sets ownership permissions for home directory
2. **[config/jupyter/before_notebook.sh](config/jupyter/before_notebook.sh)**: Mounts CVMFS, selects fastest regional server, configures environment
3. **jupyter_notebook_config.py**: Generated config that defines JupyterLab server proxies for webapps
4. **[config/jupyter/jupyterlab_startup.sh](config/jupyter/jupyterlab_startup.sh)**: Starts JupyterLab and associated services

### Core Components

**CVMFS (CernVM File System)**
- Distributes neuroimaging software containers without local storage
- Regional server selection based on latency probing (Europe/America/Asia)
- Two modes: Direct access or CDN (selected automatically)
- Configuration in [config/cvmfs/](config/cvmfs/)
- Can be disabled via `CVMFS_DISABLE=true` environment variable

**Neurocommand**
- Cloned from https://github.com/neurodesk/neurocommand during build
- Provides CLI and module system for neuroimaging tools
- Uses Lmod for module management
- Containers stored in `/neurodesktop-storage/containers`

**Webapp System**
- Webapps defined in `webapps.json` (fetched from neurocommand repo)
- [scripts/generate_jupyter_config.py](scripts/generate_jupyter_config.py) generates Jupyter Server Proxy entries
- Each webapp launches via [config/jupyter/webapp_launcher.sh](config/jupyter/webapp_launcher.sh)
- Uses Unix sockets to avoid port conflicts (`/tmp/neurodesk_webapp_{name}.sock`)

**Desktop Environment**
- LXDE (Lightweight X11 Desktop Environment)
- TigerVNC server for VNC access
- Apache Guacamole provides browser-based remote desktop
- Configuration in [config/lxde/](config/lxde/) and [config/guacamole/](config/guacamole/)

**Services**
- JupyterLab: Main interface (port 8888)
- Apache Tomcat: Serves Guacamole web application
- VNC: Desktop access via Guacamole
- SSH: Optional SSH server proxy
- Optional: Ollama for local LLMs (if `START_LOCAL_LLMS=1`)

### Directory Structure

- [config/](config/): Service configurations
  - `jupyter/`: JupyterLab config, startup scripts, webapp infrastructure
  - `guacamole/`: Remote desktop gateway config
  - `cvmfs/`: CVMFS mount configurations and keys
  - `lxde/`: Desktop environment customization
  - `firefox/`, `vscode/`, `itksnap/`: Application-specific configs
- [scripts/](scripts/): Build-time utilities
- `.github/workflows/`: CI/CD pipelines
  - `build-neurodesktop.yml`: Daily automated builds (17:00 UTC)
  - `test-cvmfs.yml`: CVMFS server health checks
  - Multi-architecture builds (amd64, arm64)


### Important Build-Time Behaviors

**Config Generation**
The Dockerfile fetches `webapps.json` from the neurocommand repository and generates `jupyter_notebook_config.py` using a template system. To add new webapps, update the source webapps.json.

**User Permissions**
The container runs as the `jovyan` user (from base Jupyter image). The `NB_UID` and `NB_GID` environment variables allow matching host user permissions.

**CVMFS Setup**
CVMFS configuration files exist for different regions and modes (direct/CDN). The startup script probes servers and copies the best config to the active location.

## Environment Variables

- `CVMFS_DISABLE`: Set to `true` to disable CVMFS mounting
- `NB_UID`, `NB_GID`: User/group IDs for permission matching
- `START_LOCAL_LLMS`: Set to `1` to enable Ollama with neurodesk model
- `NEURODESKTOP_VERSION`: Version tag (set by CI)
