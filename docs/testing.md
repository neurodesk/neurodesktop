---
title: Testing
description: Two-tier test suite, per-area focused test commands, container
  build/run modes, and the negative-test convention
parent: index.md
status: current
last-reviewed: "2026-08-31"
---

# Testing

The suite has two tiers, and which one a new test belongs in is decided by a
single question: **does it need a running container to answer?**

| | `tests/unit/` | `tests/container/` |
| --- | --- | --- |
| Runs on | a repository checkout | inside the built image |
| Command | `pytest tests/unit` | `pytest /opt/tests/` |
| Run by | the `Unit tests` workflow, on every push and pull request | the build workflows, once per test profile |
| Covers | repository sources, importable Python modules, shell scripts driven against a temporary `HOME` | mounts, installed kernels, running services, CVMFS, the shipped binaries |

Default to `tests/unit/`. Reach for `tests/container/` only when the assertion
genuinely cannot be made without the image — a service must be running, a
package must be installed, a mount must be present. A test that reads the
`Dockerfile`, parses a config file, or drives a script against `tmp_path` is a
unit test even when the thing it describes only exists in the image. The
rationale for this split is recorded in the
[test suite audit](designs/test-suite-audit.md).

Only `tests/container/` is copied into the image (together with
`conftest.py`, `testlib.py`, and `pytest.ini`), so nothing under
`tests/unit/` is available at `/opt/tests/`.

```bash
pytest tests/unit          # from a checkout, no container needed
pytest /opt/tests/         # inside the built image
```

Running `tests/unit` needs `pytest`, `httpx`, `traitlets`, and `ssh-keygen`
(`openssh-client`); see `.github/workflows/unit-tests.yml`.

Two modules need heavier optional dependencies and skip cleanly when they are
absent rather than failing a plain checkout; CI installs them, so they always
run there:

- `tests/unit/test_astra_view_graph.py` needs `astra-spec==0.0.12`,
  `astra-tools==0.2.11`, and `anywidget==0.11.0`, because it runs the released
  ASTRA validators. Those pull in ~50 further packages.
- `tests/unit/test_astra_view_filebrowser.py` needs `jupyter-server` to drive
  the file-browser server extension.

## Shared helpers

`tests/testlib.py` resolves a test's subject in whichever layout it is running
in, and is installed next to the container tier at `/opt/tests/testlib.py`
(the installed `conftest.py` puts it on the import path in the image layout):

- `resolve_source(installed, relative)` — the path the image installs it to,
  falling back to its path in the checkout. Use this for anything that ships
  both ways.
- `repo_path(relative)` — a repository source with no installed counterpart
  (the `Dockerfile`, `.github/**`). Only valid in the unit tier; it raises with
  a pointer to `tests/unit/` if called from the image.
- `load_source_module(name, installed, relative)` — import a Python source file
  resolved the same way.
- `run_cmd(cmd, cwd=, env=, timeout=)` — shell out and return
  `(exit_code, combined_output)`. `env` overlays the caller's environment.

## Focused tests by area

AGENTS.md defines which tests each area change must run; this table is the
same routing in one place. Every entry lists the checkout command first and
the in-image command second. The sections that follow explain what the
non-obvious tiers protect.

| Area | On a checkout | In the built image |
| --- | --- | --- |
| Apptainer NVIDIA auto-configuration | `pytest tests/unit/test_apptainer_nv.py` | — |
| Access-URL banner (`print_access_url.sh`) | `pytest tests/unit/test_print_access_url.py` | — |
| Sherlock launcher (`scripts/connectSherlock.sh`) | `pytest tests/unit/test_connect_sherlock.py` | — |
| Jupyter Server Proxy response limits | `pytest tests/unit/test_jupyter_server_proxy_limits.py` | `pytest /opt/tests/test_jupyter_server_proxy_limits.py`, then real large-response proxy check |
| ASTRA viewer core (adapter, graph, widget, previews) | `pytest tests/unit/test_astra_view_graph.py tests/unit/test_astra_view_packaging.py` | `pytest /opt/tests/test_astra_view_image.py` |
| File-browser ASTRA viewer (server extension, file type/factory) | `pytest tests/unit/test_astra_view_filebrowser.py` | `pytest /opt/tests/test_astra_view_image.py` |
| `astra`/`lc` installs, Lightcone skills and hooks | `pytest tests/unit/test_astra_jupyter_ai_tooling.py` | `pytest /opt/tests/test_astra_agent_skills_image.py` |
| Jupyter AI, ACP personas, collaboration/widget compatibility and server patches | see [below](#jupyter-ai-and-acp-personas) | `pytest /opt/tests/test_astra_jupyter_ai_image.py /opt/tests/test_widget_compatibility_image.py` |
| Notebook Intelligence / MyST and standalone RISE | `pytest tests/unit/test_nbi_settings_patch.py tests/unit/test_myst_build_workaround.py tests/unit/test_jupyterlab_rise_patch.py` | `pytest /opt/tests/test_nbi_labextension_patch.py /opt/tests/test_rise_slides_image.py` |
| Launcher extension, workspace link routing | `pytest tests/unit/test_workspace_link_routing.py` | `pytest /opt/tests/test_workspace_link_routing_image.py` |
| Agentic workflows under `.github/workflows/*.md` | `pytest tests/unit/test_report_job_failure_action.py tests/unit/test_agentic_maintenance_workflows.py` | — |

### Jupyter Server Proxy response limits

The unit test executes the single-load Jupyter server configuration, simulates
JupyterHub replacing Tornado's mutable client defaults, applies the anchored
Jupyter Server Proxy patch to its upstream seam, and instantiates both TCP and
Unix-socket clients to assert matching 1024 MiB buffer and body limits. A
runtime check must proxy a response larger than Tornado's 100 MiB default
through a fully initialized single-user server in a built image; the unit
construction test does not prove the full installed proxy request succeeds.

### Desktop tests

Desktop smoke tests keep Guacamole, Tomcat, VNC, and credential state in
temporary per-test homes by default. Tests that need to start the global xrdp
service are skipped unless
`NEURODESKTOP_TEST_ALLOW_GLOBAL_DESKTOP_SERVICES=1` is set. The build scripts and
GitHub Actions set this only for disposable test containers; do not set it in a
live user desktop unless stopping or reconfiguring xrdp is acceptable.

For focused Apptainer build checks:

```bash
docker buildx build --check .
docker buildx build --target apptainer --progress=plain .
```

### Workspace link routing

The unit tier asserts the interception guards in the TypeScript source; the
image tier asserts the plugin survived the labextension build, that JupyterLab
accepts it, that `jupyterlab_server` still publishes the `serverRoot` page
config option the mapping depends on, and that the `Markdown Preview` and
`HTML Viewer` factories a clicked report opens with are registered and not
disabled. Those factory names are upstream strings; if a JupyterLab upgrade
renames one, a clicked report quietly falls back to the text editor rather
than failing, which is exactly why the image tier pins them.

### ASTRA CLIs, Lightcone skills, and hooks

The image tier is the one that matters here: it drives the real hook scripts
end to end (so a missing `jq` fails loudly), asserts that exactly one `astra`
answers on `PATH`, checks that the pinned marketplace commit teaches the
schema version the installed `astra validate` speaks, and restores a throwaway
home to prove all four reproduction skills reach OpenCode. It also drives the
OpenCode adapter directly and checks that session, read, and edit hook context
is returned to the model-facing system prompt or tool output.

### Jupyter AI and ACP personas

```bash
pytest tests/unit/test_jupyter_ai_workspace.py
pytest tests/unit/test_astra_jupyter_ai_tooling.py
pytest tests/unit/test_jupyter_server_documents_patch.py
pytest tests/unit/test_neurodesktop_stream_output.py
pytest tests/unit/test_ipyniivue_patch.py
pytest tests/unit/test_widget_browser_diagnostics.py
pytest tests/unit/test_ipywidgets_control_comm_patch.py
pytest tests/unit/test_jupyterlab_widgets_patch.py
pytest tests/unit/test_jupyter_ai_acp_client_patch.py
pytest tests/unit/test_jupyter_server_mcp_patch.py
pytest tests/unit/test_coding_agents.py -k 'opencode_machine_commands or opencode_acp_exports_lmod'
# In the rebuilt image:
pytest /opt/tests/test_astra_jupyter_ai_image.py /opt/tests/test_widget_compatibility_image.py
pip check
jupyter server extension list
jupyter labextension list --verbose
```

The workspace test covers the checkout-safe hook behavior: only ``.chat``
saves seed ``AGENTS.md``, project-authored guidance is never overwritten, and
seed failures do not block chat creation. The image test drives a real
``FileContentsManager.new_untitled(..., ext=".chat")`` call against the shipped
hook and ``/opt/AGENTS.md``. The widget image test inspects the installed
JupyterLab manager bundle and requires its bounded late-model retry, because
server-side notebook output and kernel widget comms travel over independently
ordered WebSockets. It also starts Jupyter Server and headless Firefox, runs an
``HBox`` whose model comm is delayed for three seconds through the installed
server-side cell executor, re-executes the cell, and opens a second JupyterLab
client against the populated room. The replay client uses an explicit separate
workspace so JupyterLab does not relocate it away from the already-open default
workspace and abort in-flight plugin asset requests. It waits for that
workspace's plugins to activate before opening the notebook through JupyterLab's
document command, keeping document restoration out of application bootstrap. A
direct two-client control-comm check also requires each widget-state reply to
return to the client that requested it.
After execution, re-execution, and replay, the browser walks every live
``WidgetRenderer`` and requires its manager promise to resolve. It also creates
a real manager-less renderer, exposes it through an output area's child walk,
emits ``outputLengthChanged``, and requires the defensive output watch to attach
the active manager. This is the behavioral guard for that patch; bundle-marker
assertions only confirm that the intended asset was installed.

The missing-model test removes a live model from the frontend registry and
replaces ``_loadFromKernel()`` with a deterministic stub that restores the
retained model. It proves concurrent-call deduplication, the post-request model
check, cleanup, and the short negative cache. It does not prove that the real
control comm can reconstruct a deliberately dropped ``comm_open``. Triggering
that transport loss deterministically would require intercepting the kernel
WebSocket before JupyterLab receives the frame. The full browser workflow still
drives the real bulk restore during second-client replay. Missing-model recovery
also requires ``restoredStatus``; it will not start a competing restore while
the initial restore remains pending.

The browser test requires the bulk control-state reply to survive a five-second
scheduled kernel delay without entering the per-model fallback. The delay does
not block the kernel event loop, so concurrent manager requests cannot serialize
several artificial delays into the bounded frontend wait. It requires exact,
non-duplicated stream text plus a widget DOM instead of a YDoc output exception,
``model not found``,
or the unsafe renderer's ``text/plain`` fallback. The stream uses many flushed
carriage-return fragments, so the test checks both CRDT stream updates and
replay rather than only displaying separate complete stream outputs. Before
clicking Run, the test waits for the status bar to name the selected kernel and
report ``Idle``. The notebook execution indicator alone can report ``idle``
before JupyterLab has created a kernel. Its disposable Firefox profile enables
WebGL2 and permits Firefox's software fallback without forcing Mesa's driver
mode, then probes a real WebGL2 context before JupyterLab opens. Only a failed
startup capability probe replaces Firefox; the notebook and replay assertions
run once, so an application race still fails. If all fresh browser probes lack
WebGL2, the test reports the Firefox reason and omits only the NiiVue code and
canvas counts. It still executes, re-executes, and replays the stream and
delayed ``HBox``.
Timeout reports include the current WebGL renderer and context state together
with bounded Firefox and Jupyter Server log tails. The test also requires
`ipykernel` 6.31.0 as the stable main-shell widget-comm path. When WebGL2 is
available, the same browser test creates nine NiiVue models, re-executes them,
and restores them in the second client. It requires nine canvases with no
permanent ``Loading widget...`` output. It interacts with every canvas and
requires scene-model traffic to stop once idle. A preload probe counts interval
callbacks created by the shared ipyniivue asset, so the upstream 30 ms polling
loop fails even when repeated scene comparisons produce no model delta. The
ipyniivue unit test moves the installed 5 MB frontend into one content-hashed
JupyterLab asset, keeps model state inside a factory-created definition, and
requires event-driven scene synchronization plus WebGL cleanup on model
destruction. The other frontend unit tests
require changed federated chunks and remote entries to use new content-hashed
names, so an immutable cached copy cannot conceal a fix.
The same unit and image suites require the server-documents reconnect guards:
a handshake timeout must resume broadcasts without disconnecting the client,
a late SyncStep2 must still be applied, and divergent repair must delete only
Yjs item ranges absent from the server state vector. These assertions protect
against the one-blank-cell autosave regression after a server restart or room
cleanup.

### MyST and standalone RISE

The checkout tests guard the pinned MyST rebuild and the anchored RISE
page-config patch. The image test starts the installed Jupyter Server, opens a
real `/rise/<notebook>` URL in the image's headless Firefox through WebDriver
BiDi, and waits for the first slide's text. An HTTP 200 response is insufficient:
RISE can serve its shell while a frontend activation error leaves the page
blank.

### ASTRA viewer

The unit tier uses the released ASTRA validators against checked-in projects,
tests external-analysis and child-universe confinement, and covers G1-G7 and
the trust states the viewer derives from run evidence.
`tests/fixtures/astra-bet` is the canonical worked spec both tiers read: the
unit tier reads it from the checkout and the image tier validates its installed
copy at `/opt/neurodesktop/examples/astra-bet`, so a broken example fails the
build rather than reaching a user. The image tier is otherwise intentionally
small — the installed package and pins, the real vendored frontend, and the
file-browser server extension.

## Negative Test Convention

When adding tests for pipeline or module-loading workflows, always include a
negative test alongside the positive happy-path test. The negative test should
use `module load funny-name-tool`, which is a non-existent module, and assert
that the workflow fails with a non-zero exit code and does not produce output.

This guards against silent failures caused by `set +euo pipefail` and `|| true`
patterns in workflow scripts.

A non-zero exit code on its own is **not** enough. `module load` also exits
non-zero when Lmod is not installed at all, so a negative test that asserts only
on the exit status passes on a machine with no Lmod, no CVMFS, and no pipeline
tool — which is exactly how
`test_nipype.py::test_nipype_nonexistent_module_fails` used to pass outside the
container. Every negative test must therefore:

1. assert the environment it is about is really present (e.g.
   `/usr/share/lmod/lmod/init/bash` exists), and
2. assert the pipeline produced **no output file**, not just that something
   returned non-zero.

`test_nextflow.py::test_nextflow_nonexistent_module_fails` is the reference
shape.

## Building the Container

Build the Docker image locally:

```bash
docker build . -t neurodesktop:latest
```

Build and run using the convenience script:

```bash
./build_and_run.sh
```

The [`build_and_run.sh`](../build_and_run.sh) script builds the image and runs it
with recommended settings, including persistent home, CVMFS enabled, and port
8888.

## Modes of `build_and_run.sh`

The script always builds the image first, then dispatches based on the first
argument:

- `./build_and_run.sh` — Launch the container interactively with the classic
  Docker settings (privileged, root, CVMFS enabled).
- `./build_and_run.sh test` — Build, start a single container with the default
  configuration, and run `pytest /opt/tests/` inside. Tears down the container
  afterwards.
- `./build_and_run.sh hpc [user] [uid] [gid]` — Launch an **interactive**
  session that simulates an Apptainer HPC deployment: no `--privileged`, no
  `--user=root`, no sudo, a non-`jovyan` container user (default `sciget`, UID
  `5000`), host-owned bind-mount over `/home/jovyan`, and `APPTAINER_CONTAINER=1`.
  Jupyter is exposed on `127.0.0.1:8888`. Use this to reproduce HPC-only bugs
  locally.
- `./build_and_run.sh hpctest [user] [uid] [gid]` — Same HPC simulation
  envelope as `hpc`, but runs detached and executes `pytest /opt/tests/`
  inside. Tears down the container and removes the temp `/etc/passwd` /
  `/etc/group` / home files on exit.
- `./build_and_run.sh fulltest` — Runs the test suite across **five
  configurations in parallel** and dumps each container's captured log once
  they have *all* finished: the four `std` configs (`CVMFS_DISABLE ∈ {false,
  true}` × `GRANT_SUDO ∈ {no, yes}`) plus the `hpc` Apptainer simulation
  (`sciget`, UID `5000`, no root). Fastest wall-clock path — roughly one
  container-start's worth of time regardless of how many configs you add —
  but you get no live progress, only the final summary + per-config logs.
  Exits non-zero if any configuration fails.
- `./build_and_run.sh fulltest_verbose` — Same set of five configurations,
  but runs them **sequentially** and streams each container's pytest output
  to your terminal live. Much slower (≈5× the `fulltest` wall-clock) but you
  can watch per-test progress, see failures in real time, and abort early
  with Ctrl-C. Each config is torn down before the next one starts. A
  summary table is printed at the end listing PASS/FAIL for each config.

### HPC simulation details

The `hpc` and `hpctest` modes, and the `hpc` leg of `fulltest`, share a common
launch envelope that mirrors what Apptainer does on shared HPC nodes:

- `--user <uid>:<gid>` with a non-1000 UID (so `jovyan`-specific paths are
  exercised against a different real user).
- A generated `/etc/passwd` and `/etc/group` bind-mounted read-only, adding the
  simulated user alongside `jovyan` so tools like `id`, `vncserver`, and `sshd`
  resolve the UID.
- A temporary host directory bind-mounted over `/home/jovyan` so the container
  starts with an empty home the HPC user can populate.
- `APPTAINER_CONTAINER=1` and `APPTAINER_NAME` exported so every
  `is_apptainer_runtime()` check branches into its unprivileged path.
- `CVMFS_DISABLE=true` because CVMFS needs FUSE and capabilities that the
  simulated unprivileged environment does not grant.

After `hpc` or `hpctest`, tear everything down with:

```bash
docker rm -f neurodesktop-hpc   # or neurodesktop-hpctest
rm -rf /tmp/neurodesktop-hpc-home.* /tmp/neurodesktop-hpc-passwd.* /tmp/neurodesktop-hpc-group.*
```
