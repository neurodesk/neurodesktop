---
title: Testing
description: Two-tier test suite, per-area focused test commands, container
  build/run modes, and the negative-test convention
parent: index.md
status: current
last-reviewed: "2026-09-19"
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
`tests/unit/` is available at `/opt/tests/`. The build normalizes that test
tier and the installed ASTRA example to be world-readable so tests run as
`jovyan` even when the source checkout was created with a restrictive umask.

```bash
pytest tests/unit          # from a checkout, no container needed
pytest /opt/tests/         # inside the built image
```

Running `tests/unit` needs `pytest`, `httpx`, `traitlets`, `jq`, and `ssh-keygen`
(`openssh-client`); see `.github/workflows/unit-tests.yml`. The terminal-creation
tests stub `curl` but use the real `jq` to parse responses.

Install those with the interpreter's own site packages rather than `pip install
--user`. `tests/unit/test_agentic_validation.py` runs
`config/agentic/validate.py`, which starts the frozen baseline with `python -I`
so a candidate patch cannot reach it through the environment. Isolated mode
also drops user site packages, so a `--user` install of `pytest` or of its
`pygments` dependency leaves that subprocess unable to import pytest, and the
test fails for a reason unrelated to the code under test.

Two modules need heavier optional dependencies and skip cleanly when they are
absent rather than failing a plain checkout; CI installs them, so they always
run there:

- `tests/unit/test_astra_view_graph.py` needs the exact `astra-spec`,
  `astra-tools`, and `anywidget` versions declared by
  `extensions/astra-viewer/pyproject.toml`, because it runs the released ASTRA
  validators. CI installs that local project so the test environment cannot
  drift from the viewer package metadata. Those dependencies pull in ~50
  further packages.
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
| Jupyter isolated build dependencies | `pytest tests/unit/test_jupyter_build_constraints.py tests/unit/test_jupyterlab_slurm_build.py` | Fresh isolated wheel builds for Slurm and launcher |
| CVMFS inventory health | `pytest tests/unit/test_cvmfs_inventory_check.py` | Live mirror workflow |
| Nightly JupyterHub probe (terminal creation, FSL commands) | `pytest tests/unit/test_jupyter_terminal_creation.py tests/unit/test_github_workflows.py` | Live `JupyterHub API Testing` workflow |
| Lmod extension listing default | `pytest tests/unit/test_lmod_extensions.py` | `pytest /opt/tests/test_lmod_avail_extensions.py` |
| Apptainer NVIDIA auto-configuration | `pytest tests/unit/test_apptainer_nv.py` | — |
| Access-URL banner (`print_access_url.sh`) | `pytest tests/unit/test_print_access_url.py` | — |
| Sherlock launcher (`scripts/connectSherlock.sh`) | `pytest tests/unit/test_connect_sherlock.py` | — |
| T3 Code server, web UI, lifecycle, and packaging | `pytest tests/unit/test_t3_code_server.py tests/unit/test_t3_code_web.py` | `pytest /opt/tests/test_t3_code_server_image.py /opt/tests/test_t3_code_web_image.py` |
| Tailscale binary packaging | `pytest tests/unit/test_tailscale_packaging.py tests/unit/test_audit_image_versions.py` | `pytest /opt/tests/test_tailscale_image.py` |
| Guided T3/Tailscale setup | `pytest tests/unit/test_t3_neurodesk_setup.py tests/unit/test_t3_code_server.py` | `pytest /opt/tests/test_tailscale_image.py /opt/tests/test_t3_code_server_image.py` |
| Jupyter Server Proxy response limits | `pytest tests/unit/test_jupyter_server_proxy_limits.py` | `pytest /opt/tests/test_jupyter_server_proxy_limits.py`, then real large-response proxy check |
| ASTRA viewer core (adapter, graph, widget, previews) | `pytest tests/unit/test_astra_view_graph.py tests/unit/test_astra_view_packaging.py` | `pytest /opt/tests/test_astra_view_image.py` |
| File-browser ASTRA viewer (server extension, file type/factory) | `pytest tests/unit/test_astra_view_filebrowser.py` | `pytest /opt/tests/test_astra_view_image.py` |
| `astra`/`lc` installs, Lightcone skills and hooks | `pytest tests/unit/test_astra_jupyter_ai_tooling.py tests/unit/test_lightcone_cli_patch.py` | `pytest /opt/tests/test_astra_agent_skills_image.py` |
| Jupyter AI, ACP personas, collaboration/widget compatibility and server patches | see [below](#jupyter-ai-and-acp-personas) | `pytest /opt/tests/test_astra_jupyter_ai_image.py /opt/tests/test_widget_compatibility_image.py` |
| Notebook Intelligence / MyST and standalone RISE | `pytest tests/unit/test_nbi_settings_patch.py tests/unit/test_myst_build_workaround.py tests/unit/test_jupyterlab_rise_patch.py` | `pytest /opt/tests/test_nbi_labextension_patch.py /opt/tests/test_rise_slides_image.py` |
| Launcher extension, workspace link routing | `pytest tests/unit/test_workspace_link_routing.py` | `pytest /opt/tests/test_workspace_link_routing_image.py` |
| Subscription agent workflows and failure reporting | `pytest tests/unit/test_agentic_*.py tests/unit/test_report_workflow_failure.py` | Worker Docker sandbox probe |

### Jupyter Server Proxy response limits

The unit test executes the single-load Jupyter server configuration, simulates
JupyterHub replacing Tornado's mutable client defaults, applies the anchored
Jupyter Server Proxy patch to its upstream seam, and instantiates both TCP and
Unix-socket clients to assert matching 1024 MiB buffer and body limits. A
runtime check must proxy a response larger than Tornado's 100 MiB default
through a fully initialized single-user server in a built image; the unit
construction test does not prove the full installed proxy request succeeds.

### Tailscale binaries

The checkout test guards architecture selection, pinned checksums, extraction,
and cleanup. The image test checks both executable versions and starts a fresh
userspace daemon as the unprivileged notebook user. It requires the local API
to report `NeedsLogin`, then stops the daemon. Run it with no network, no
capabilities, and no `/dev/net/tun` to check the restricted-pod case. It uses
temporary state and never authenticates to a tailnet. A real desktop pairing
over Tailscale remains a separate manual check requiring a tailnet login.

The [guided setup](architecture/t3-code.md#connect-through-tailscale-inside-the-container)
unit tests simulate CLI responses and terminal input to cover login, reuse,
conflicting Serve configurations, desktop confirmation, identity mismatches,
token-free diagnostics, and a single pairing link built for the tailnet
address instead of T3's container-address URL. The image test also starts and
reuses a real daemon through the installed wizard and checks that it has its
own session. A separate image test mints a link through the installed `t3`
CLI, so a pinned-version bump that changes those flags or their JSON fails in
CI rather than during a manual desktop pairing.
For manual acceptance, run `t3_neurodesk_setup` in a JupyterLab terminal,
follow its desktop connectivity check, complete pairing in T3, and verify
the remote providers. Then rerun `t3_neurodesk_setup --check`.

### T3 Code server

The web unit tests require `jupyter-server-proxy` and Node.js. CI installs the
local T3 extension to supply the proxy dependency. They cover the URL-prefix
adapter, upstream drift, credential stripping, and fetch/WebSocket behavior.
The browser image test opens the installed T3 application with the JupyterLab
launcher and requires automatic pairing and a live WebSocket without entering
a token. It reloads using the scoped session cookie and checks launcher reuse. It rejects unauthenticated HTTP
and WebSocket requests and cookie-authenticated POSTs without Jupyter XSRF,
including the automatic session endpoint.
It runs at both `/` and a JupyterHub-style `/user/t3-test/` prefix.
The prefixed case also runs with JupyterHub's cookie-authenticated GET XSRF
policy, so native imports of every startup bundle must pass the proxy's
static-asset checks, including filenames containing additional dots.

The real-server image test also waits for the Codex provider probe to report
its CLI version. This exercises T3's login-shell PATH reload and catches
interactive wrapper banners that corrupt the app-server JSON stream. An
unauthenticated Codex account is acceptable; a protocol decoding error is not.

The checkout test drives the process supervisor with a real temporary child
process. It also checks the pinned package, image cleanup, server extension,
provider launchers, and direct Docker port settings. The image test starts the
installed T3 server, loads its native PTY module, and verifies that the build
removed foreign native payloads and the duplicate Claude binary.

After an image build, pair the matching T3 Code desktop release through port
3773. Start a harmless provider thread, restart the container with the same
home volume, and confirm that the environment reconnects with its project and
provider settings intact.

### Desktop tests

Desktop smoke tests keep Guacamole, Tomcat, VNC, and credential state in
temporary per-test homes by default. Tests that need to start the global xrdp
service are skipped unless
`NEURODESKTOP_TEST_ALLOW_GLOBAL_DESKTOP_SERVICES=1` is set. The build scripts and
GitHub Actions set this only for disposable test containers; do not set it in a
live user desktop unless stopping or reconfiguring xrdp is acceptable.

For focused Apptainer build checks:

```bash
pytest tests/unit/test_apptainer_crypto_security.py
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

The coding-agent image test initializes all three ACP implementations and
requests a session without credentials or a model prompt. For Codex and Claude,
an executable shim records the arguments before running the image CLI. A
successful session or the protocol's authentication-required response is
accepted only after the expected CLI invocation is observed. Other errors,
process exits, and timeouts fail the test. This check covers adapter upgrades
that use an image CLI outside the adapter's declared dependency range.

The Jupyter AI image test also imports Notebook Intelligence and the MCP v1
``mcp.server.fastmcp`` API. ``jupyter server extension list`` returns success
even when an individual extension reports an import error, so its process exit
status is not a sufficient compatibility assertion by itself.

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
uses a deterministic state injector for the eventual kernel-owned model. It
proves concurrent-call deduplication, the post-request model check, cleanup,
the short negative cache, and that every recovery load runs while
``_kernelRestoreInProgress`` is true. A second transition check leaves
``get_model()`` and ``_loadFromKernel()`` intact and fails their control-comm
creation and comm-info fallback. Two real ``WidgetRenderer`` instances must
show the error, retain their MIME models, and recover from one restored signal.
Two adjacent restored signals must create one view, and direct model
registration must wake a renderer whose recovery already failed.

The test still does not prove that the real control comm reconstructs a
deliberately dropped ``comm_open``. Deterministically creating that state means
intercepting the kernel WebSocket before JupyterLab registers the comm; deleting
an existing manager model is not equivalent because JupyterLab still owns its
comm. The full browser workflow drives real bulk restoration during
second-client replay. Missing-model recovery also requires ``restoredStatus``;
it will not start a competing restore while the initial restore remains
pending.

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
The installed-asset checks also parse the complete patched server-documents
and ipyniivue bundles with Node. They require server-side execution to grant
trust inside the request ``try``, after both kernel guards and the scheduled
callback, and to restore the previous value on every request failure path.
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

### Nightly JupyterHub probe

`.github/workflows/jupyter_test_main.yml` drives each live instance over the
JupyterHub REST API: it starts the user server, opens a terminal, and runs the
basic and FSL commands through that terminal's WebSocket. The Hub reports
`"ready": true` before the user pod can reach the Hub API to authorize
incoming tokens, so the first terminal request often comes back as HTTP 500
with a `Failed to connect to Hub API` body.
`.github/workflows/create_jupyter_terminal.sh` absorbs that window: it retries
transport failures, HTTP 408/409/425/429 and 5xx, and success statuses that
carry no terminal name, and it fails immediately on a rejection that will not
change, such as HTTP 403. `TERMINAL_CREATE_ATTEMPTS` and
`TERMINAL_CREATE_DELAY` bound the wait. The unit tier drives the helper
against a stubbed `curl`, so it needs no network and no listening socket.

### Lmod extension listing

The unit tier asserts that `environment_variables.sh` exports
`LMOD_AVAIL_EXTENSIONS=no` and that a user override wins. That says nothing
about whether Lmod still honors the variable, so an Lmod upgrade that renamed
or dropped it would leave the unit test green and `ml av` cluttered again. The
image tier runs the real `ml av` against a synthetic module that provides an
extension and requires the extension to be listed with the variable set to
`yes` and absent by default.

### T3 desktop linking

`tests/unit/test_t3_connect.py` covers device-code parsing, cancellation and expiry,
restart protection, persisted-link recovery, relay delay, and credential isolation.
Container browser tests cover the authenticated Connect endpoint under root and
JupyterHub prefixes. Real account authorization and external tunnel readiness
require an approved test account; CI must not approve a live device grant.
The T3 provider probe must reject both wire-message and payload-decoding errors,
including when an old home-installed Codex would otherwise precede the image CLI.


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
