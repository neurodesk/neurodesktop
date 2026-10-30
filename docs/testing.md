---
title: Testing
description: Two-tier test suite, per-area focused test commands, container
  build/run modes, and the negative-test convention
parent: index.md
status: current
last-reviewed: "2026-10-27"
---

# Testing

The optional [Kasm image](architecture/kasm.md) has a separate runtime smoke
test: `bash scripts/verify_kasm_image.sh IMAGE`. It starts a desktop, runs
`tests/container/test_kasm_workspace.py`, and recreates the container to check
home persistence. Its checkout tests run with
`pytest tests/unit/test_kasm_workspace.py`. The ordinary smoke test disables
CVMFS. The explicit `--science` option launches a privileged container and
also runs `tests/container/test_kasm_science.py`, which checks CVMFS-backed FSL
arithmetic through nested Apptainer. Kasm platform lifecycle checks still
require an actual Kasm Workspaces deployment.

Kasm submission packaging is covered by `pytest tests/unit/test_kasm_submission.py`.
Workflow input resolution is covered by `pytest tests/unit/test_kasm_release_workflow.py`.
The [publishing guide](architecture/kasm-publishing.md) describes the release
workflow's runtime tests, SBOM, vulnerability gate, and remaining platform checks.

The suite has two tiers, and which one a new test belongs in is decided by a
single question: **does it need a running container to answer?**

| | `tests/unit/` | `tests/container/` |
| --- | --- | --- |
| Runs on | a repository checkout | inside the built image |
| Command | `pytest tests/unit` | `pytest /opt/tests/` |
| Run by | the `Unit tests` workflow, on every push and pull request | PR candidate and release workflows, once per test profile |
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

Running `tests/unit` needs GCC, X11 development headers, Xvfb, and `xrandr`
for the Glass helpers,
Python 3.15, Node.js 24, `pytest`, `httpx`,
`traitlets`, `requests`, `ipykernel`, `websocket-client`, `nibabel`, `numpy`,
Git, curl, `jq`, GNU `mkdir`, and `ssh-keygen` (`openssh-client`); see
`.github/workflows/unit-tests.yml`. The terminal-creation
tests stub `curl` but use the real `jq` to parse responses.

The installed workspace-link browser test starts Jupyter directly with a fresh
home. It sets `NBI_TOUR_CONFIG_PATH` to the image's installed default and checks
the authenticated capabilities response before clicking launcher tiles. This
preserves the normal startup configuration that disables NBI's first-run tour.
The same journey adds real launcher-model entries before clicking Slurm in two
new launchers. The click waits for the tile to remain visible and unobscured
as those entries change its position.

The launcher DOM regressions, including T3 iframe file-link capture, reloads,
disposal, and the T3 Connect panel, also require jsdom and TypeScript.
ASTRA document tests additionally load the
real JupyterLab registry, document context, and Lumino widgets. The document
harness supplies an inert `ResizeObserver` adapter because jsdom does not
compute layout. These cases cover document lifecycle rather than toolbar
resizing. Install the dependencies
in a temporary directory and expose that directory when running the suite:

```bash
npm install --prefix /tmp/neurodesk-launcher-tests --no-audit --no-fund jsdom@26.1.0 typescript@5.9.3 esbuild@0.28.2 \
  @jupyterlab/docregistry@4.6.5 @jupyterlab/coreutils@6.6.5 \
  @jupyterlab/services@7.6.5 @lumino/widgets@2.10.0
export NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES=/tmp/neurodesk-launcher-tests/node_modules
pytest tests/unit
```

Missing launcher test dependencies fail with setup guidance instead of skipping.
The unit-test workflow installs these same pinned dependencies automatically.
The offline worker's `config/agentic/Dockerfile` uses the same DOM package pins.
Renovate updates all three locations together, and the dependency-contract tests
compare the worker and this command against the workflow's current pins.
Review JupyterLab/Lumino upgrades against the JupyterLab shipped in the image.
Regression coverage for extraction, grouping, and missing or stale pins runs
with `pytest tests/unit/test_renovate_dom_harness.py`.
The suite also runs real ESLint checks. Install their locked dependencies with
`npm ci --prefix tools/javascript-lint --ignore-scripts --no-audit --no-fund`.

The unit-test workflow also builds the agentic worker and validates an unchanged
checkout through its offline, credential-free Docker path. This checks the
worker's actual native and JavaScript prerequisites as the unit suite changes.
The [agentic setup guide](agentic-maintenance.md#verify-setup-with-one-issue)
provides the local canary command. Frozen and candidate suite results are
reported separately, and both must pass.

Install the Python test dependencies in the interpreter's own site packages
rather than with `pip install --user`. `tests/unit/test_agentic_validation.py` runs
`config/agentic/validate.py`, which starts the frozen baseline with `python -I`
so a candidate patch cannot reach it through the environment. Isolated mode
also drops user site packages, so a `--user` install of `pytest` or of its
`pygments` dependency leaves that subprocess unable to import pytest, and the
test fails for a reason unrelated to the code under test.
Use `testlib.repo_path()` to locate repository sources in unit tests. The frozen
baseline redirects that helper to the candidate checkout; source roots derived
from the test file's `__file__` instead point into the tests-only snapshot.

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

## Code quality checks

The [code quality workflow](../.github/workflows/code-quality.yml) runs on every
pull request and push to `main`, independently of unit tests and image acceptance.
Run the same checks locally from a checkout with Python 3.15 and Node.js 24:

```bash
python3 -m venv .venv-quality
. .venv-quality/bin/activate
python -m pip install -r requirements-quality.txt
bash scripts/install_quality_tools.sh "$VIRTUAL_ENV/bin"
npm ci --prefix extensions/neurodesk-launcher --no-audit --no-fund
npm ci --prefix tools/javascript-lint --no-audit --no-fund
bash scripts/check_quality.sh all
```

The binary installer supports Linux amd64 and arm64 and verifies pinned
SHA-256 checksums. The Dockerfile check also requires Docker with Buildx and
access to its daemon. Individual gates are available as `python`, `shell`,
`workflow`, `javascript`, `frontend`, `format`, and `docker` arguments to
`scripts/check_quality.sh`. Checks report failures without changing source files.

Ruff checks owned Python, including tests and the extensionless provenance CLI.
Its configuration excludes frozen upstream Python fixtures and notebooks; the
FSL notebook fixture is covered by its execution test. ShellCheck discovers
shell files by extension or shebang, including extensionless launchers.
Actionlint checks workflow expressions and embedded shell commands.

The `javascript` gate checks the syntax of owned scripts and DOM test drivers,
then lints the same files with ESLint. `javascript_files()` in
`scripts/check_quality.py` is the only file scope. It excludes `node_modules`,
the frozen widget-manager excerpts, and the launcher's generated `lib/` and
`labextension/` output; a directory merely named `lib` or `vendor` elsewhere is
still checked. ESLint, its rule packages, and
[`eslint.config.mjs`](../tools/javascript-lint/eslint.config.mjs) live in
`tools/javascript-lint/` with their own lockfile, so the gate does not depend on
the launcher's install. The rules cover correctness only: undefined and unused
names, unreachable code, and unsafe control flow such as fall-through,
`return` in `finally`, and constant conditions. Formatting is not linted.

The configuration maps each file to the runtime that executes it. `.cjs` files
are Node CommonJS and `.mjs` files are Node ES modules. Each `.js` file is named
with its runtime: CommonJS build helpers, the OpenCode ES-module plugin, the
ASTRA anywidget module, classic browser scripts injected into Guacamole and the
T3 iframe, and the Firefox preference file. An unlisted `.js` file gets only
ECMAScript globals, so Node or browser API use fails until the file is
classified. Globals supplied by a runtime contract are declared next to it, for
example the `__T3_PREFIX__` value that `web.py` substitutes and the jsdom
globals that a DOM driver installs. The checks run on untyped JavaScript, so
they do not prove promise handling. Browser adapters rely on behavioral
rejection and disposal tests instead. The frontend gate compiles the launcher
against its locked dependencies and checks typed promise handling with ESLint.
Compilation runs without the Python-side JupyterLab bundling step.

Formatting and Python typing start with explicit, small scopes. The formatter
scope is declared in `scripts/check_quality.py`; strict Python typing is declared
in `mypy.ini`. Expand these scopes in reviewed changes rather than reformatting
unrelated files to fix a correctness finding.

The strict scope covers the provider credential policy, the OpenCode
`opencode.json` update helper, and the T3 package's process side: the supervisor, its environment naming, the Jupyter lifecycle
adapter and the extension entry point. The same modules are in the Ruff
formatter scope. mypy skips unlisted modules, so a checked module's callers
are listed with it. The lifecycle adapter's `ExtensionApp` base is typed by
following only `jupyter_server.extension`, `jupyter_core` and `traitlets`,
pinned in `requirements-quality.txt`. Remaining limitations:

- The T3 `web` and `connect` adapters are outside the scope, so the lifecycle
  adapter sees `ConnectManager` and the handler classes as untyped, and those
  adapters' calls into the supervisor are not checked. They need typed
  `tornado` and `jupyter_server.base` APIs, and `jupyter-server-proxy` has
  no type information.
- `ExtensionApp.log` is an untyped traitlets trait upstream, so the logger
  passed to the supervisor is not checked.

The quality runner `scripts/check_quality.py` is in both scopes too, with a
different target. `check_quality.sh` runs it with the `python3` on `PATH`,
which is Python 3.10 on the Ubuntu 22.04 runner host, while `mypy.ini` targets
the image's Python 3.12. The `python` gate therefore checks the runner's
`RUNNER_HOST_TYPED_SCOPE` in a second strict mypy run with `--python-version
3.10`, and `ruff.toml` gives it a `py310` target. Do not add it to the `files`
list in `mypy.ini`. Its tests drive it only through the command line, so the
runner has no Python callers to check.

Python embedded in shell heredocs is not covered by Ruff and continues to rely
on execution tests. Keep exceptions local and explain external runtime
dependencies such as Jupyter-injected globals.

[Issue #1074](https://github.com/neurodesk/neurodesktop/issues/1074) moves
substantial heredocs into owned modules, one block per change. The OpenCode
`opencode.json` model refresh is now
`config/agents/opencode_update_config.py`. That module is in the formatter and
strict typing scopes. `tests/unit/test_opencode_update_config.py` tests it
directly, and `tests/unit/test_coding_agents.py` covers the wrapper contract.
The NBI mirror of the OpenCode model selection is now
`config/agents/nbi_mirror_opencode.py`, in the same scopes.
`tests/unit/test_nbi_mirror_opencode.py` tests it directly, and
`tests/unit/test_nbi_opencode_sync.py` covers the `nbi_setup.sh` contract.
These substantive heredocs remain:

| File | Block | Lines |
| --- | --- | --- |
| `config/agents/nbi_setup.sh` | Repair wiped NBI endpoint settings | ~80 |
| `config/agents/nbi_setup.sh` | Inject the Neurodesk API key into NBI | ~70 |
| `config/agents/nbi_setup.sh` | Refresh a running NBI server | ~50 |
| `config/agents/nbi_setup.sh` | Sync the Brain Researcher MCP entry | ~40 |
| `config/agents/opencode` | Apply the Brain Researcher MCP entry | ~40 |
| `Dockerfile` | Wrap kernel specs to re-source the environment | ~30 |
| `config/agents/opencode` | Seed the TUI sidebar default | ~25 |

The OpenCode wrapper's Neurodesk model-list parser remains inline.
Ollama tags, show metadata, and context limits share the typed helper.

The workflow uses stable job names so repository rules can require each check.
Unit and installed image tests remain necessary for authentication, startup
ordering, filesystem permissions, UI disposal, and installed application behavior.

### Required checks

The `protect-default` repository ruleset (id 24091967) targets the default branch.
It keeps the deletion and non-fast-forward rules and the organization-admin
bypass. It also requires these check contexts from GitHub Actions
(integration id 15368):

| Workflow | Required check context |
| --- | --- |
| Code quality | `Python lint and types` |
| Code quality | `Shell, workflows and formatting` |
| Code quality | `JavaScript syntax` |
| Code quality | `Frontend types and promises` |
| Code quality | `Dockerfile checks` |
| Unit tests | `pytest tests/unit` |
| PR image acceptance | `Installed image (amd64)` |
| PR image acceptance | `Installed image (arm64)` |

The required `pytest tests/unit` context aggregates
`Checkout unit tests` and `Offline worker validation`, which run in parallel.
It succeeds only when both jobs succeed. A failed, cancelled, skipped, or missing
dependency result fails the aggregate.

Branches need not be up to date with `main` before merging. A missing, pending,
or cancelled required check blocks a normal merge. Every pull request to `main`
must emit all eight contexts, so these workflows must not gain workflow-level
path filters. Renaming a required job also requires updating the ruleset in the
same change. No merge queue is configured; add a `merge_group` trigger to each
workflow before enabling one. Read the active configuration back with:

```bash
gh api repos/neurodesk/neurodesktop/rulesets/24091967
gh api repos/neurodesk/neurodesktop/rules/branches/main
```

Disposable PR #1101 verified the gate on 2026-10-14 and was closed unmerged.
Its `mergeStateStatus` was `BLOCKED` when a real `JavaScript syntax` failure was
the only unsuccessful required check, while the image checks were in progress,
and after the image run was cancelled. After the fix and a rerun, all eight
checks passed and the state became `CLEAN`. Organization admins keep the
ruleset bypass, so they can still merge past a failing check; agents must not
use it.

## Behavioral coverage

Checkout tests execute scripts, HTTP handlers, and DOM interactions. Source
assertions establish packaging contracts only. Expected inputs and outputs come
from worked examples, documented behavior, or independent upstream fixtures.
The widget patch CLI uses frozen excerpts from the pinned upstream wheel under
`tests/fixtures/widget-manager/`; changing its search constants cannot silently
change those fixtures too.

CVMFS ranking fixtures download real payloads from local HTTP servers. A curl
wrapper controls only reported transfer times for speed-dependent cases, keeping
HTTP status, remote addresses, transfer failures, and body validation real. These
cases verify ranking decisions, not host network performance.

The ASTRA renderer cases run its shipped ESM in jsdom. They cover filtering,
layout ordering, warnings, selection, scroll retention, and shared-model
cleanup. The ASTRA HTTP cases start real token-authenticated Jupyter servers at
`/` and `/user/alice/`, exercise both asset and graph routes, and reject workspace
escapes through `spec`, `universe`, and `run` request parameters. Document tests
exercise evidence discovery, save, refresh, retry, stale requests, and disposal
through the real document factory. The installed browser journey opens the
example from the file browser, selects a universe and evidence mode, saves
changed files through Jupyter Contents, and refreshes the graph.

The Bash negative case requires a successful calculation and the specific
missing-command error in executed cell output. An unrelated nbconvert failure
fails the test. DataLad creates a local annexed dataset, saves and clones it,
retrieves exact content, drops the clone's content, and retrieves it again.
Neither test needs an external dataset or account.

The FSL notebook workflow executes code over the authenticated kernel
WebSocket and requires a matching successful reply plus idle status. It saves
and retrieves the executed notebook through Contents, validates code-cell
outputs, and checks finite, compatible brain and binary-mask images against
the input. A printed success message cannot hide a failed subprocess. Retrieved counts
and outputs must match the results captured during execution. The independent
brain/skull fixture under `tests/container/fixtures/fsl-bet/` is executed by
`test_fsl_bet_image.py`; it requires known brain tissue to survive and excludes
the surrounding shell, so a consistent but anatomically wrong mask fails.

Desktop acceptance in
[test_desktop_application_workflows.py](../tests/container/test_desktop_application_workflows.py)
checks document
content after LibreOffice format conversions, saved VS Code editor content,
a segmentation exported from ITK-SNAP, and FSLeyes pixels from a known volume.
The VS Code journey clicks the file through its BiDi element reference so a
late Explorer layout change cannot invalidate recorded screen coordinates.
`test_vscode_browser_workflow.py` moves the row between discovery and native
input, then requires the same edit and authenticated save to complete.
These tests start their own Xtigervnc display through
[native_desktop_driver.py](../tests/container/native_desktop_driver.py), so they
need no running desktop. The driver supplies a mode-0600 RFB Unix socket
beside its log while keeping both RFB and X11 TCP listeners disabled.
TigerVNC 1.15 requires an incoming RFB
endpoint even for local X11-only tests. The strict WebGL fixture uses this
same driver. If the container refuses abstract Unix sockets, the
driver retries with only the `/tmp/.X11-unix` file socket. It waits for the
complete newline-terminated `-displayfd` announcement before closing the pipe;
closing after the digits alone can make Xtigervnc abort its final write.
Checkout tests cover fragmented announcements, invalid replies, and early EOF.
The FSLeyes batch check uses the upstream `fsleyes_unfiltered render` entrypoint
so captured output remains available. It requires exit status zero and the
expected image pixels. The FSL image build repairs the default `fsleyes` filter's
stream cleanup. Its [release tests](https://github.com/neurodesk/neurocontainers/blob/main/recipes/fsl/fulltest.yaml)
check filtering, stdout/stderr restoration, application exit codes, and rendering
without an X display. [Issue #1012](https://github.com/NeuroDesk/neurodesktop/issues/1012)
records the shutdown race in older builds that could return 120 after rendering.
Timed-out module commands terminate their process groups and report captured
output. Module commands and ITK-SNAP's first window share a 600-second launch
budget because a cold CVMFS cache must fetch the application container first.
ITK-SNAP waits for the selected segmentation to render before using its
save menu; failures include the application's log. Native window discovery and
focus run in one bounded X server grab so closing application dialogs cannot
race the focus request. The driver also replaces Xlib's default error and I/O
error handlers, which would otherwise end pytest with exit status 1 and no
report; a rejected request or a lost display fails the test with the display
log. `test_desktops.py` checks that contract by provoking both faults. Closing the
driver restores the previous process-wide Xlib handlers; a separate display
checks both handlers after the closed driver has been garbage-collected.
`NEURODESKTOP_REQUIRE_APPLICATIONS=1` prevents the required science profile
from silently skipping when CVMFS is disabled.

The RDP test uses the service provisioned by root startup. It runs as the
notebook user without unrestricted sudo and requires pixels over Guacamole.
A read-only X11 probe identifies the new notebook-user XRDP process and its
display and authority file. Before Alt+F2, it waits for WM registration and a
mapped LXDE panel. Before typing, it requires a mapped, active Run dialog with
one of its children holding keyboard focus. These bounded waits also read the
Guacamole tunnel and fail on disconnects or protocol errors. The test launches
a command entirely through Guacamole keyboard input and checks its output file.
The marker lives in a fresh, session-owned temporary directory, so root-run tests
remain writable by the desktop user and cannot accept a prior result. A login
screen alone does not satisfy this check. Enable it only in disposable test
containers with `NEURODESKTOP_TEST_ALLOW_GLOBAL_DESKTOP_SERVICES=1`. The HPC
profile skips RDP because unprivileged startup does not provision that service.
The same test checks that sesman's private session directory and mode-0660
Xorg sockets grant the `xrdp` group access while excluding other users.

Pull-request CI runs the checkout tier and native image acceptance. Release
workflows also validate installed applications before promotion. Neither a
version check nor a passing checkout suite proves every shipped application works. The
[behavior audit](designs/test-behavior-audit-2026-10-03.md) records the coverage
and remaining acceptance gaps.

## Bundled code-server security regressions

`tests/unit/test_code_server_dependency_security.py` checks the builder's
security version floors, installed paths, version assertions, and package
cleanup. `tests/unit/test_audit_image_versions.py` checks npm release reporting
for both build arguments without network access.

`tests/container/test_code_server_dependency_security.py` loads the installed
packages through Node.js. The shell-quote cases require a `TypeError` for
LF, CR, U+2028, and U+2029 after a comment, and execute safe quoted arguments
through `/bin/sh` to check literal preservation. The proxy-addr cases reject
trust bypass through a malformed IPv4-mapped IPv6 CIDR. Ordinary IPv4 and
correctly mapped CIDRs must still resolve forwarded clients only through
trusted proxies.

## Pull request image acceptance

[PR image acceptance](../.github/workflows/pr-image-validation.yml) builds the
run SHA on disposable native amd64 and arm64 GitHub runners. Its token has only
`contents: read`; checkout credentials are not persisted. The candidate stays
local to each runner, and the workflow neither publishes nor promotes tags.
Both architectures run the package-only sudo and foreign-UID HPC suites.
Before runtime acceptance, each candidate passes the release workflow's
critical vulnerability and secret scan with the same ignore policy. The scan
includes image layers, so removing flagged examples in a later layer does not
repair a package installation that already committed them.
The amd64 runner also provisions CVMFS and runs the `acceptance` profile.
Before mounting, it uses the image's server-selection CLI to verify and rank
mirrors from the runner's network. No verified mirror fails this setup step.
The workflow reports the active CVMFS host and mount statistics after acceptance,
including failed runs.

```bash
bash .github/scripts/validate_image_runtime.sh IMAGE acceptance
```

That profile requires scientific and desktop application workflows, followed
by [test_niivue_rendering_image.py](../tests/container/test_niivue_rendering_image.py)
with `NEURODESKTOP_REQUIRE_WEBGL=1`.
The graphics test starts a private Xtigervnc display without network listeners
and uses Mesa software rendering in Firefox. It requires volume pixels and
browser-to-kernel scene updates in a compact viewport. It measures the canvas
again before dragging and reads scene coordinates from the active notebook's
kernel through JupyterLab's authenticated execution API. Reads queue behind
the widget's first-render comm traffic, so the test waits up to 90 seconds for
a successful kernel reply and idle state, then checks the initial and changed
coordinates. A kernel that stays busy fails with its reported status. Missing WebGL is a failure. The portable
widget suite remains available on displayless runners.

The validator installs cleanup before startup, rejects a readiness timeout or
malformed HTTP status, and propagates application and graphics test failures.
Before removing a started container after a failed validation stage, it
prints the last 120 container log lines so startup and service diagnostics
survive cleanup.
CVMFS-backed application validation currently applies to amd64; arm64 still
runs the portable installed-image suite.

## Image release validation

Production, test, and development image workflows build native amd64 and arm64
candidates tagged `run-<run_id>-<run_attempt>-<arch>`. Runtime tests and critical
vulnerability scans consume those candidates, pulling them through
[`scripts/retry.sh`](../scripts/retry.sh) so a transient registry error does not
fail validation. Only after every required job
passes does `merge-manifests` promote the architecture, date, and `latest` tags
and copy them to configured registries. Failed candidates remain available for
diagnosis under their run tags; they do not replace release tags.

The production workflow retries a failed architecture build once after 30
seconds. Both attempts use the same source SHA, build arguments, cache, and
run-specific candidate tag, so a transient upstream registry failure can recover
without changing the artifact that later test and scan jobs consume. A failed
second attempt still fails the build job and prevents promotion.

`pytest tests/unit/test_trivy_policy.py` checks the packaging contract for the
twelve kernel CVE exceptions added after issues #1142 and #1152. Each
exception requires
the exact assessed Ubuntu `linux-libc-dev` package version and an expiry no
later than 2026-11-20. The image scans apply these rules through Trivy's shared
ignore file. The [build reference](architecture/build.md#publication)
records why the headers remain installed and how the exceptions are bounded.

`pytest tests/unit/test_conda_rattler_security.py` protects the temporary
removal of the vulnerable optional Rattler solver without adding a Trivy
exception. The installed-image `test_conda_rattler_security_image.py` confirms
that both Rattler packages and their native module are absent, `libmamba` is
selected, the vulnerable `py-rattler` line is disallowed, and an installation
from a generated local channel succeeds without network access. Harmless fixture
packages named `py-rattler` test rejection of 0.27.0 and acceptance of 0.28.0,
checking enforcement
of the vulnerable-version restriction without blocking the fixed line.

`pytest tests/unit/test_python_ca_security.py` protects the fixed `pip`,
`certifi`, and `uv` version floors for their bundled CA stores. The
installed-image suite's `test_python_ca_security.py` parses both the main
`certifi` bundle and pip's vendored copy and rejects the two distrusted
emSign G3 roots. The ASTRA agent-skills test continues to execute
the image's pinned `uv` binary.

`pytest tests/unit/test_brotli_security.py` protects the direct fixed Brotli
version and its build-time metadata check. The installed-image tests require
one fixed package release and exercise compression and decompression through
the shipped extension module.

The checkout tests execute the build's `uv` version check with release banners
containing target or commit metadata. They require the exact pinned version
and reject a failing command even when it prints the expected release.

The preparation job chooses one UTC build timestamp for both architectures and
publication, so date tags match the version baked into the image even across
midnight. All checkouts use the run's source SHA. Scheduled runs build that revision even
when today's date tag exists, using the registry build cache. Development
builds remain manual. When retrying an image release, rerun **all jobs** so the
new attempt builds the candidate tags its validation jobs expect.

Both architectures exercise sudo-disabled, sudo-enabled, package-only sudo,
and HPC simulation profiles. Only amd64 exercises CVMFS; arm64 CVMFS remains
excluded until its upstream content and routing are available. PR acceptance
requires WebGL2 on amd64 through the image's software-rendered display.

Each test step runs [the runtime validation command](../.github/scripts/validate_image_runtime.sh)
with the candidate image and the `regular`, `hpc`, or `acceptance` profile. The command
owns startup, readiness polling, security-policy checks, pytest, and cleanup.
The workflows own candidate pulls and host CVMFS provisioning. CVMFS jobs in
the production, scheduled test, and development image workflows run the same
verified mirror selection as PR acceptance before mounting the generated
configuration. Mounting the static GeoAPI list instead left cold application
launches at the mercy of whichever mirror it picked; see
[issue #1021](https://github.com/NeuroDesk/neurodesktop/issues/1021). They fail setup if no mirror passes and
report the active CVMFS host, mount statistics, and recent client syslog
entries even after a failed test.
The steps use `exec` so the command retains the workflow shell's cancellation
boundary. On both disposable Ubuntu 24.04 runners, PR image validation captures
and temporarily disables AppArmor's restriction on unprivileged user namespaces
before the regular runtime suite, including its Glass scientific isolation probe.
This follows [Apptainer's Ubuntu namespace prerequisite](https://apptainer.org/docs/admin/main/user_namespace.html#user-namespace-requirements).
Setup fails if the policy cannot be read or changed. An `always()` step restores
the captured policy on both architectures after validation, including on failure
or cancellation. The HPC profile retains Docker's default security settings and
skips the Glass-specific nested namespace probe.

These jobs also restore the host CVMFS client cache from earlier runs through
[`cvmfs_ci_cache.sh`](../.github/scripts/cvmfs_ci_cache.sh). A cold cache made
application tests depend on live mirror throughput, and a run with every mirror
slow timed out FSLeyes; see
[issue #1077](https://github.com/NeuroDesk/neurodesktop/issues/1077). The client
uses the restored directory as an alien cache, which has no quota database to
disagree with a snapshot. Cache keys roll over each ISO week, so the first run
of the week fetches live from CVMFS. Runs on `main`, including the daily
scheduled production build, save the cache that pull requests restore. Other
refs save only when nothing was restored. The scheduled
[CVMFS health workflow](../.github/workflows/test-cvmfs.yml) never uses this
cache and continues to check the live mirrors.

The regular and acceptance profiles poll readiness up to 60 times and fail
before pytest if polling expires. The HPC profile also checks container
liveness during its 90 readiness attempts and fails before pytest if the
container exits or polling expires.

The command installs its EXIT trap before startup or temporary-file allocation.
The trap removes the container, repairs HPC home ownership, and deletes HPC
temporary files on success or failure. Cleanup preserves the original failure
status and fails an otherwise successful job if cleanup fails. Checkout tests
execute the command with temporary Docker and sleep adapters and check workflow
calls. Run the focused checks with:

```bash
pytest tests/unit/test_image_runtime_validation.py tests/unit/test_build_neurodesktop_workflow.py \
  tests/unit/test_cvmfs_ci_cache.py
```

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
| Image packaging layers | `pytest tests/unit/test_image_packaging_layers.py tests/unit/test_myst_build_workaround.py` | `pytest /opt/tests/test_image_size_hygiene.py /opt/tests/test_additional_components.py` and image layer inventory |
| PyJWT asymmetric/HMAC key confusion | `pytest tests/unit/test_pyjwt_security.py tests/unit/test_audit_image_versions.py` | `pytest /opt/tests/test_pyjwt_security.py` |
| Brotli decoder out-of-bounds write | `pytest tests/unit/test_brotli_security.py tests/unit/test_audit_image_versions.py` | `pytest /opt/tests/test_brotli_security.py` |
| Python bundled CA trust | `pytest tests/unit/test_python_ca_security.py tests/unit/test_audit_image_versions.py` | `pytest /opt/tests/test_python_ca_security.py /opt/tests/test_astra_agent_skills_image.py` |
| ghapi public credential examples | `pytest tests/unit/test_ghapi_examples.py tests/unit/test_image_packaging_layers.py` | `pytest /opt/tests/test_image_size_hygiene.py` and unchanged image secret scan |
| Anthropic SDK public credential examples | `pytest tests/unit/test_anthropic_examples.py tests/unit/test_image_packaging_layers.py` | `pytest /opt/tests/test_anthropic_examples.py` and unchanged image secret scan |
| CVMFS mirror selection | `pytest tests/unit/test_cvmfs_selection.py tests/unit/test_cvmfs_server_inventory.py` | Run the selector with temporary config/cache paths, then verify a mounted read; see [CVMFS](architecture/cvmfs.md) |
| CVMFS inventory health | `pytest tests/unit/test_cvmfs_mount_check.py tests/unit/test_cvmfs_inventory_check.py` | Live mirror workflow |
| Object-storage inventory health | `pytest tests/unit/test_objectstorage_check.py` | Live `Test Objectstorage` workflow |
| Nightly JupyterHub probe (terminal creation, FSL commands) | `pytest tests/unit/test_jupyter_terminal_creation.py tests/unit/test_github_workflows.py` | Live `JupyterHub API Testing` workflow |
| Lmod extension listing default | `pytest tests/unit/test_lmod_extensions.py` | `pytest /opt/tests/test_lmod_avail_extensions.py` |
| Lmod cache warm-up | `pytest tests/unit/test_warm_module_cache.py` | `pytest /opt/tests/test_lmod_cache_warmup.py` |
| Interactive module banner | `pytest tests/unit/test_module_banner.py` | — |
| Apptainer NVIDIA auto-configuration | `pytest tests/unit/test_apptainer_nv.py` | — |
| Access-URL banner (`print_access_url.sh`) | `pytest tests/unit/test_print_access_url.py` | — |
| Sherlock launcher (`scripts/connectSherlock.sh`) | `pytest tests/unit/test_connect_sherlock.py` | — |
| Neurocommand container-store layout | `pytest tests/unit/test_container_storage_link.py` | `pytest /opt/tests/test_neurocommand_storage_image.py` |
| Guacamole/Tomcat listener confinement | `pytest tests/unit/test_configure_tomcat.py tests/unit/test_desktop_launcher_modes.py` | `pytest /opt/tests/test_desktops.py -k guac_api_connections` |
| SSH startup opt-out | `pytest tests/unit/test_sftp_sshd_security.py tests/unit/test_ssh_proxy_policy.py tests/unit/test_connect_sherlock.py` | `pytest /opt/tests/test_ssh_proxy_policy.py /opt/tests/test_desktops.py -k 'ssh_proxy or ssh-disabled or vnc_backends_bind_localhost_only'` |
| T3 Code server, web UI, lifecycle, and packaging | `pytest tests/unit/test_t3_code_server.py tests/unit/test_t3_startup_settings.py tests/unit/test_t3_code_web.py` | `pytest /opt/tests/test_t3_code_server_image.py /opt/tests/test_t3_code_web_image.py` |
| Tailscale binary packaging | `pytest tests/unit/test_tailscale_packaging.py tests/unit/test_audit_image_versions.py` | `pytest /opt/tests/test_tailscale_image.py` |
| Guided T3/Tailscale setup | `pytest tests/unit/test_t3_neurodesk_setup.py tests/unit/test_t3_code_server.py` | `pytest /opt/tests/test_tailscale_image.py /opt/tests/test_t3_code_server_image.py` |
| Jupyter Server Proxy response limits | `pytest tests/unit/test_jupyter_server_proxy_limits.py` | `pytest /opt/tests/test_jupyter_server_proxy_limits.py` |
| ASTRA viewer core (adapter, graph, widget, previews) | `pytest tests/unit/test_astra_view_graph.py tests/unit/test_astra_view_packaging.py tests/unit/test_astra_view_renderer.py` | `pytest /opt/tests/test_astra_view_image.py` |
| File-browser ASTRA viewer (server extension, file type/factory) | `pytest tests/unit/test_astra_view_filebrowser.py tests/unit/test_astra_view_http.py` | `pytest /opt/tests/test_astra_view_image.py` |
| `astra`/`lc` installs, Lightcone skills and hooks | `pytest tests/unit/test_astra_jupyter_ai_tooling.py tests/unit/test_lightcone_modules.py tests/unit/test_lightcone_evidence.py` | `pytest /opt/tests/test_astra_agent_skills_image.py` |
| ASTRA run provenance sidecars and the spec agreement check | `pytest tests/unit/test_astra_provenance.py` | `pytest /opt/tests/test_astra_agent_skills_image.py` |
| Jupyter AI, ACP personas, collaboration/widget compatibility and server patches | see [below](#jupyter-ai-and-acp-personas) | `pytest /opt/tests/test_astra_jupyter_ai_image.py /opt/tests/test_widget_compatibility_image.py` |
| Notebook Intelligence / MyST and standalone RISE | `pytest tests/unit/test_nbi_settings_patch.py tests/unit/test_myst_build_workaround.py tests/unit/test_jupyterlab_rise_patch.py` | `pytest /opt/tests/test_nbi_labextension_patch.py /opt/tests/test_rise_slides_image.py` |
| Launcher extension, workspace link routing | `pytest tests/unit/test_workspace_link_routing.py tests/unit/test_jupyterlab_url.py` | `pytest /opt/tests/test_workspace_link_routing_image.py` |
| Slurm dashboard launcher and user identity | `pytest tests/unit/test_launcher_webapps.py tests/unit/test_slurm_user_identity.py tests/unit/test_jupyterlab_slurm_build.py` | `pytest /opt/tests/test_slurm.py /opt/tests/test_workspace_link_routing_image.py` |
| Subscription agent workflows and failure reporting | `pytest tests/unit/test_agentic_*.py tests/unit/test_report_workflow_failure.py` | Worker Docker sandbox probe |

### Jupyter Server Proxy response limits

The unit test executes the single-load Jupyter server configuration, simulates
JupyterHub replacing Tornado's mutable client defaults, applies the anchored
Jupyter Server Proxy patch to its upstream seam, and instantiates both TCP and
Unix-socket clients to assert matching 1024 MiB buffer and body limits. The
image test also starts a Jupyter server under a user URL prefix and resets the
HTTP client defaults after configuration loading. It transfers 101 MiB through
both TCP and Unix-socket backends and checks the complete payload's size and
SHA-256 digest. The backend data is generated locally; no external service is
needed.

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

The supervisor waits for HTTP 200 from the local environment endpoint before
reporting readiness. A TCP listener alone is insufficient: T3 can accept an
early request without answering it. The image probe retries short HTTP requests
within a 20-second deadline and verifies the environment label.

The real-server image test checks both startup settings policies for fresh
profiles and exact legacy defaults, then waits for the Codex provider probe
to report its CLI version. This exercises T3's login-shell PATH reload and catches
interactive wrapper banners that corrupt the app-server JSON stream. An
unauthenticated Codex account is acceptable; a protocol decoding error is not.

The same test checks that the seeded OpenCode instance reports enabled, which
is the state T3 withholds from a driver it ships disabled. T3 probes OpenCode
lazily, so that record can still be the unchecked placeholder; a separate image
test covers the start contract instead. T3 reads a semantic version from
`--version`, refuses releases below its floor, then runs `serve` and waits for
the `opencode server listening` line carrying the server URL. OpenCode's ACP
probe does not cover this path, because T3's driver speaks the OpenCode SDK
rather than ACP.

The checkout test drives the process supervisor with a real temporary child
process. It also checks the pinned package, image cleanup, server extension,
provider launchers, and direct Docker port settings. Startup settings preparation
checks provider ownership and update notice suppression together.
`tests/unit/test_t3_startup_settings.py` covers one JSON read, at most one atomic
replacement, unchanged bytes on a no-op, and independent update suppression
when provider data is malformed. The image test starts the
installed T3 server, loads its native PTY module, and verifies that the build
removed foreign native payloads and the duplicate Claude binary.

After an image build, pair the matching T3 Code desktop release through port
3773. Start a harmless provider thread, restart the container with the same
home volume, and confirm that the environment reconnects with its project and
provider settings intact.

### T3 and Ollama completion

`tests/unit/test_opencode_ollama.py` drives shared discovery against local HTTP
fixtures and executes quiet launchers with fresh homes. It covers the total
deadline, metadata fallback, empty and failed listings, credential isolation,
concurrent process-local observations, preserved configuration, interpolation
rejection, and `--version`. The plugin driver executes the actual native config
hook against resolved fixture objects.

Inside the image, `pytest /opt/tests/test_t3_ollama.py` starts a private T3 server
with the image-owned OpenCode path. It authenticates through T3's bearer-session
CLI and uses the pinned RPC schemas to refresh the provider, create a project,
and complete a turn with a controlled Ollama fixture. It requires the discovered
model in T3's picker and a completed assistant result in the durable projection.
The persistent OpenCode config must retain its original bytes. A concurrent
quiet launch during the controlled turn must leave that turn on its original
attempt. The same module tests native JSONC merge precedence, saved offline
configuration, successful empty catalogs, and ACP stdout as JSON. It starts an
OpenCode server outside a project, creates that project after startup, and
compares its custom endpoint, model limits, and selection with native OpenCode.
Environment and file canaries must never appear in discovered model metadata;
intentional user configuration references must still resolve.

The ACP stdout check sets `npm_config_offline=true` for its child process. Its
local Ollama plugin needs no npm packages. This keeps the 40-second protocol
check independent of OpenCode's first-use registry installation while still
loading the installed plugin and executing the real image binary. Other
first-use and completion checks retain native dependency installation.

For the live gate, run the image on a Docker network where an Ollama server with
a downloaded model is reachable as `host.docker.internal:11434`. Set
`NEURODESKTOP_TEST_OLLAMA_MODEL` to that model ID, then run
`pytest /opt/tests/test_t3_ollama.py`. The live case always uses the default URL.
For a host-installed Ollama, Docker also needs
`--add-host=host.docker.internal:host-gateway`. The live case skips when no model
ID is supplied. It allows ten minutes for CPU inference and disables OpenCode's
auxiliary title agent in that test's inline configuration, so the live request
measures the chat completion. The controlled fixture also covers title requests.
The controlled completion remains required without external services.

### Desktop tests

The private VS Code socket check in
[`tests/container/test_security_policy.py`](../tests/container/test_security_policy.py)
checks mode `0600` and the private directory before waiting for HTTP 200 from
`/healthz`. Code-server starts listening before registering its routes, so an
early HTTP 404 or request timeout is retried within the same 45-second startup
deadline. Each socket operation uses the time remaining, and fragmented status
lines are read through their terminating CRLF before checking for HTTP 200.
A child exit or an expired deadline reports the last request outcome and server
log.
The root-run check also requires another UID to be denied access.
[`pytest tests/unit/test_security_policy_readiness.py`](../tests/unit/test_security_policy_readiness.py)
exercises delayed route readiness, fragmented responses, request timeouts,
deadline enforcement, permanent failure, and early process exit with a
controlled clock.
The shipped command starts code-server through
[`config/jupyter/code_server_ready.py`](../config/jupyter/code_server_ready.py),
which only moves the socket to the proxy's path after `/healthz` returns 200, so
Jupyter Server Proxy never sees that early 404.
[`pytest tests/unit/test_code_server_ready.py`](../tests/unit/test_code_server_ready.py)
runs the launcher against a fake code-server. It covers the hidden socket while
routes return 404, mode `0600` after the rename, signal forwarding, early exit,
cleanup of code-server when the launcher is killed, and an orphan from a
timed-out launch shutting down while the next launch starts.

Desktop smoke tests keep Guacamole, Tomcat, VNC, and credential state in
temporary per-test homes by default. VNC tunnel cases cover fresh and legacy
homes without the XDG config parent, an absolute override, and an ignored
relative override. SSH-disabled cases start with stale SFTP port state,
require the SFTP connection to be disabled without a new SSH daemon, and
receive desktop frames through the authenticated VNC tunnel. The combined-backend
case exercises the template that enables SFTP by default. The installed
SSH proxy tests load the shipped Jupyter configuration and verify that the
`sshd` route and its host-key generation disappear only when
`NEURODESKTOP_DISABLE_SSH=true`.
The VNC tunnel cases require saved settings to survive TigerVNC's migration
and actual desktop frames to arrive over the authenticated tunnel.
Tests that need to start the global xrdp service are skipped unless
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

The unit tier executes the complete TypeScript module with Node.js 24's type
stripping and VM modules. Jupyter and DOM dependencies are substituted, while
path mapping, click handling, line references, viewer selection, directory
routing, and error reporting execute unchanged. The image tier opens JupyterLab
at both root and user-prefixed URLs, clicks absolute workspace links, and
requires Markdown and HTML viewers to open in the main panel. JupyterLab may
update the address bar to a document route such as `/lab/tree/a%20report.md`.
The URL must stay on the same origin and within the configured `/lab` route,
including its user prefix. Raw filesystem URLs such as `/home/jovyan/...` fail
this check. `tests/unit/test_jupyterlab_url.py` covers accepted document routes
and rejected navigation targets.
It also opens the Slurm dashboard through its Neurodesk launcher tile, checking
its label and icon on two launcher renders against the installed Slurm extension.
Before its single mouse click, the test waits for the tile's position and size
to remain stable across animation frames and for its center to be unobscured.
The click targets the tile's BiDi element reference; the dashboard must then
become visible without retrying the click.
This browser test configures `SlurmCommandPaths.squeue_path` as `/usr/bin/true`
and verifies the HTTP endpoint returns an empty queue using that command,
so it does not depend on a host Slurm controller in the HPC simulation. The
separate `test_slurm.py` suite exercises the real scheduler and batch commands.
Installation and shipped page-configuration checks remain in the image tier.

### ASTRA CLIs, Lightcone skills, and hooks

The image tier is the one that matters here: it drives the real hook scripts
end to end (so a missing `jq` fails loudly), asserts that exactly one `astra`
answers on `PATH`, checks that the pinned marketplace commit teaches the
schema version the installed `astra validate` speaks, and restores a throwaway
home to prove all four reproduction skills reach OpenCode. It also drives the
OpenCode adapter directly and checks that session, read, and edit hook context
is returned to the model-facing system prompt or tool output.

### Lightcone 0.5 native execution

Run `pytest tests/unit/test_lightcone_modules.py tests/unit/test_lightcone_evidence.py
 tests/unit/test_astra_view_graph.py` from the checkout. The evidence fixture
contains real local-worker 0.5 status, manifests, and RO-Crate. Lifecycle unit
cases double the native CLI and prove ownership and failure propagation, not
live Slurm compatibility. Viewer cases prevent canonical root evidence from
attaching to a child output with the same name. Generated module rerun commands
execute in an isolated test interpreter and refuse missing or changed engines
before invoking the worker.

In the built image, run `pytest /opt/tests/test_astra_lc_run_image.py` with Slurm
enabled. This executes a real native allocation, a Python probe, materialization,
a no-op, freshness checks, a recipe rebuild, viewer ingestion, and cleanup.
It also verifies private per-user connection storage without changing home
permissions and retains rejection of writable connection-path ancestors.
In the privileged science profile, also set
`NEURODESKTOP_TEST_LIGHTCONE_SCIENCE=1`. That test requires pinned FSL through
CVMFS and nested Apptainer on a real Lightcone worker, compares every output
voxel, and checks the manifest's module pins and `fs: open` attestation.
It then executes the generated record with `datalad rerun`, compares every voxel
again, and requires a new execution timestamp with the same environment identity
and module attestation.
The image runtime driver's `acceptance` mode sets this flag, so the amd64
PR image job runs the science gate after mounting CVMFS.
Repeat the installed tests on native arm64. Missing live services leave these
gates unverified; the local-worker fixtures do not substitute for them.

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
pytest tests/unit/test_coding_agents.py tests/unit/test_agent_shell_environment.py
# In the rebuilt image:
pytest /opt/tests/test_astra_jupyter_ai_image.py /opt/tests/test_widget_compatibility_image.py
pip check
jupyter server extension list
jupyter labextension list --verbose
```

The agent-shell unit tests execute fresh child Bash shells through the shared
setup and provider launchers. They cover profile-path preference, legacy layout
fallbacks, strict scripts, inherited initialization, and preflight failures.
`pytest /opt/tests/test_agent_shell_environment.py /opt/tests/test_slurm.py`
checks installed module loading and batch initialization in a running image.

The coding-agent image test initializes all three ACP implementations and
requests a session without credentials or a model prompt. For Codex and Claude,
an executable shim records the arguments before running the image CLI. A
successful session or the protocol's authentication-required response is
accepted only after the expected CLI invocation is observed. Other errors,
process exits, and timeouts fail the test. This check covers adapter upgrades
that use an image CLI outside the adapter's declared dependency range.

The installed T3 startup cases exercise fresh and legacy defaults. They wait
for Claude's completed health probe, compare its version with the image CLI,
and require either a ready provider or the expected sign-in requirement for
an empty credential store.

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

Two kernel-connection regressions run against the installed server. One opens
a second kernel WebSocket and requires the first to keep receiving replies. The
other opens a notebook on a fresh kernel, which rejects widget restoration, and
requires the manager's retries to keep the notebook's kernel WebSocket open.

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
permanent ``Loading widget...`` output. It interacts with the first,
volume-bearing canvas and requires scene-model traffic to stop once idle. A
preload probe counts interval callbacks created by the shared ipyniivue asset,
so the upstream 30 ms polling
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

The document browser test checks the refreshed title, trust badge, selected
universe, Evidence mode, and SVG nodes together in one DOM evaluation. A save
can deliver a delayed document notification after the manual refresh completes,
so the viewer can briefly show its loading state again. The test polls for the
complete result within its existing deadline and sends the Refresh click once.

ASTRA browser clicks wait for the target to be stable and for at least one
interior point to be unobscured, then use its native BiDi element reference and
the exposed point's relative offset. A layout change between discovery and
input therefore keeps the click on the intended element. The installed Firefox
regressions in `test_astra_browser_click_image.py` move the target immediately
before input, partially or briefly cover it before clicking, and require a
persistently covered target to fail without clicking. The readiness wait
expires before the BiDi transport timeout.

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

The valid-image FSL operation is submitted by
`.github/workflows/run_fsl_terminal_probe.sh`. It keeps the original WebSocket
stream, waits for an explicit completion marker, and takes its authoritative
output snapshot only after the socket and input writer have stopped. This
preserves a final stdout frame delivered as the socket closes and reports an
explicit remote exit status when image creation or `fslmaths` fails. Checkout
tests drive the helper with a fake WebSocket client, including completion in
the connection's final frames, echoed input, failures, timeouts, and process
cleanup. Run `pytest tests/unit/test_github_workflows.py` for these regressions.
The default wait remains 24 polls at five seconds with a 120-second input
hold-open; `FSL_PROBE_ATTEMPTS`, `FSL_PROBE_DELAY`, and `FSL_PROBE_HOLD_OPEN`
allow shorter waits in checkout tests. When stopping the WebSocket client and
input writer, the helper sends TERM and escalates to KILL after
`FSL_PROBE_STOP_TIMEOUT` seconds per child (default 10; `0` kills
immediately), so a child that ignores TERM cannot hang the probe. All four
values are non-negative integers read as decimal, even with leading zeros.
Checkout tests run the helper in its own process group and kill the whole
group on timeout, so fake clients cannot outlive the test. Both success and
completion markers are required. These tests do not establish live Hub or FSL runtime health.

The start and cleanup steps read the default server's state through
`.github/workflows/jupyterhub_server_state.sh`, which parses the Hub's user
model with `jq` into `ready`, `pending:<action>`, `starting`, or `stopped`.
It prefers `servers[""]` and falls back to the top-level `server` and
`pending` fields. The Hub keeps the `server` key as `null` once a server
stops, so cleanup waits for `stopped` rather than for the key to disappear.
Cleanup repeats the stop request while a timed-out spawn is still pending.
The unit tier runs both workflow steps against a stubbed `curl` and `sleep`.

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


## Scientific workflows and writable directories

The CVMFS, Nipype, Nextflow, and Snakemake arithmetic tests use the shared
`image_math_case` fixture in `tests/conftest.py`. It creates a 3×3×3 NIfTI image
with nonzero voxel values. Each runner executes `fslmaths -mul 2`; the assertion
loads the output and checks every voxel and the affine. These tests skip only
when CVMFS is explicitly disabled. A missing module or invalid output fails.
Each execution has a 600-second timeout to allow a cold Apptainer launch.

The Slurm batch test waits for successful job completion and verifies a file
written by the job. A 180-second deadline bounds queueing and execution, and
cleanup cancels the submitted job if needed.

The Slurm environment-retrieval regression completes a small job, submits a
larger job with an `afterok` dependency, then completes another small job on
the same worker. It covers `--export=ALL`, `NIL`, `NONE`, selected-variable exports, and
explicit `--get-user-env`, checks job ID, hostname, process identity and supplied
variables in the output, and fails immediately if the worker exits. Requests
use 1 CPU/1 GB followed by 4 CPUs/12 GB when the configured node has capacity;
smaller test nodes use their configured limits. Run it in a Docker container
without `CAP_SYS_ADMIN` under the default seccomp policy to cover the denied
PID-namespace path. Privileged image checks alone cannot exercise that denial.
`bash scripts/verify_slurm_image.sh IMAGE` creates a disposable container with
default Docker permissions, bootstraps local Slurm as root, runs the Slurm tests
as the notebook user, then runs `test_slurm_multiuser.py` as root. That test creates
two temporary users with distinct login profiles, checks all export modes and
submitted-value precedence, verifies the compatibility library stays out of job
processes, and repeats after a real worker reconfiguration. A failing login profile
must requeue and hold the job without killing the worker. The test removes its users
and accounting associations. The notebook-user suite also verifies that completed
and cancelled batch jobs retain their exact Lightcone-style comment in `sacct`.
The script removes the container on success or failure. PR image
acceptance runs this check on both amd64 and native arm64 candidates.

Both `/home/jovyan` and `/neurodesktop-storage` are required writable directories
in the Docker and HPC simulation profiles. Each receives one complete CRUD test
inside a unique temporary directory. A missing directory fails instead of
removing itself from parametrization. This checks accessibility, not whether a
particular directory is backed by a bind mount.

The office association updater runs on temporary desktop files in
`tests/unit/test_office_mimeapps.py`. Coverage includes natural version ordering,
per-MIME selection, unrelated settings, repeat execution, and failure without
modifying defaults when MIME declarations are missing. The image tier retains
the checks against installed desktop associations.

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

1. execute a successful prerequisite check, such as loading FSL and importing
   the pipeline library;
2. use valid input so malformed data cannot cause the expected failure;
3. check that the error identifies the deliberately missing module; and
4. assert that the pipeline produced **no output file**. When module loading
   gates execution, also check that the interface was never entered.

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

## VirtualGL and GPU rendering

Run checkout diagnostics and packaging checks with:

```bash
pytest tests/unit/test_virtualgl.py tests/unit/test_audit_image_versions.py
```

The installed checks in `tests/container/test_virtualgl.py` execute the shipped
VirtualGL version command, diagnostic help, and Mesa rendering in a real
TigerVNC display, both directly and through VirtualGL's EGL interposer.
Ordinary CI does not require a GPU. For a GPU worker, run:

```bash
docker exec -e NEURODESKTOP_REQUIRE_GPU=1 -e VGL_DISPLAY=egl0 \
  -u jovyan YOUR_RUNNING_GPU_CONTAINER \
  pytest /opt/tests/test_virtualgl.py -v
```

The container must already have an allocated NVIDIA GPU and injected graphics
libraries. `NEURODESKTOP_REQUIRE_GPU=1` selects the hardware acceptance test;
missing devices, failed EGL, or a software renderer fail rather than skip.
The test creates its own virtual display and uses the requested EGL device.
It proves image-local rendering only. Repeat rendering and application checks
inside the rebuilt FreeSurfer container after the
[tool integration changes](designs/gpu-tool-integration.md), and verify
assigned-device isolation on a multi-GPU worker. See the
[assessment](designs/gpu-desktop-sessions.md#proposed-acceptance-gates) for
application, transport, performance, and platform coverage.

## Startup performance regressions

### Glass runtime regressions

Run the Glass helper and startup checks on a checkout:

```bash
pytest tests/unit/test_glass_helpers.py tests/unit/test_glass_startup.py tests/unit/test_glass_runtime.py tests/unit/test_glass_packaging.py tests/unit/test_startup_security.py
```

The helper tests execute the compiled binaries through test-owned transport
adapters. An independent X client under Xvfb checks host and guest clipboard
transfer, repeated copies from one window, empty text, and closed requesters.
Resize tests check framed dimensions and the real `xrandr` execution boundary.
They do not establish native CC frame presentation or display resizing.
Startup tests execute the access-URL watcher decision and scientific environment
settings. Installed-image checks verify directory traversal, root ownership,
and isolation between Apptainer writable sessions. The Glass scientific isolation
probe runs as the notebook user in the privileged regular image profile and on
native hosts. It exercises the image-owned writable-tmpfs and read-only CPU-info
bind options in a user namespace, with temporary home and sandbox directories.
Like Glass tool invocations, it shares the caller's PID namespace. This preserves
compatibility with the outer `/proc/cpuinfo` bind installed during ARM startup;
an added PID namespace would require a fresh proc mount that the kernel rejects
when the parent proc filesystem has that overmount. It skips nested HPC
environments identified by the same Apptainer and Singularity markers as the installed security tests. The unprivileged Docker
HPC profile blocks the user namespaces required by this Glass-specific probe;
its runtime security settings stay unchanged. Namespace or Apptainer failures
in the regular and native profiles still fail the probe. Native CC acceptance
remains in [the Glass deployment contract](architecture/glass.md#acceptance).

### Shared startup regressions

The Lmod cache checks use a local module tree with the installed Lmod. They
verify the reported cache directory under a custom `HOME`, with and without
`XDG_CACHE_HOME`, private cache permissions and ownership, and discovery of new
modules after warm-up invalidates an existing cache. Modulefiles record their
evaluations so an unchanged warm `module avail` must reuse the cache without
evaluating them again. These checks need no CVMFS server or network access.

The menu-update and startup-ownership checks run with:

```bash
pytest tests/unit/test_neurodesk_update.py tests/unit/test_startup_security.py
```

They execute consecutive updates against a local Git origin, check launcher
restoration after success and failure, reject root and read-only updates, and
cover ownership migration without following symbolic or hard links.
`tests/container/test_security_policy.py` checks the installed menu entry,
checkout ownership, root refusal, and package-only sudo restrictions.

Run the startup checks on a checkout:

```bash
pytest tests/unit/test_startup_batching.py tests/unit/test_wait_for_jupyter.py tests/unit/test_nbi_opencode_sync.py tests/unit/test_startup_performance_fixes.py tests/unit/test_detect_resource_limits.py tests/unit/test_host_slurm_environment.py
```

These tests cover workspace quarantine in a single Python invocation, default
restoration and ownership repair, SSH permission inheritance, NBI boot setup
without live refresh, custom Jupyter ports and base paths, and independent
CVMFS and Slurm execution. The restoration tests exercise `.bashrc_append`
against existing and dangling links, checking target contents and metadata
without permitting sudo, touch, or tee calls. They also cover ordinary files,
repeat runs, a symlinked `HOME`, and the permission fallback.

Resource-limit detection is sourced from the
helper against temporary cgroup v2 and v1 trees, with Slurm allocation
overrides and malformed or missing limit files. Host Slurm configuration is
sourced from its helper against a temporary root with real Unix sockets,
covering `slurm.conf` search order, the local `neurodesktop` config, MUNGE
and `sack.socket` discovery, stale values, and the startup warnings that the
quiet shell call omits. HTTP checks use temporary localhost servers.
Readiness deadlines run on a controlled clock instead of real elapsed
time, so scheduling delay cannot decide whether the helper probes at all.
See the [startup flow](architecture.md#container-initialization-flow) for
runtime behavior. Built-image service checks remain in
`tests/container/test_startup_modes.py`.

## FreeBrowse document viewer

`pytest tests/unit/test_freebrowse.py` executes frozen fork integration
fixtures and executes the document factory and context menu. It checks default
viewer registration, embedded document tabs, rename/disposal behavior,
authenticated contents-service downloads, temporary blob URLs, NIfTI and
NiiVue document routing, and special filenames
under standalone and JupyterHub base paths. Real Jupyter HTTP tests verify
viewer assets require authentication, GET/HEAD work, and traversal is rejected.
The fixtures come from the `FREEBROWSE_REF` commit in the Dockerfile.
JupyterHub cookie-authentication tests exercise its real XSRF decision
through HTTP for same-origin module/style requests, invalid and absent cookies,
cross-site and missing metadata, mutations, and traversal. Only the Hub token
lookup is replaced. CI installs `jupyterhub==6.1.0` for this regression.
Renovate groups this pin with the offline worker's JupyterHub pin, and a
dependency-contract test checks parity with the documented version.
The installed-image suite repeats those cases against the shipped handler in
[`test_freebrowse_hub_image.py`](../tests/container/test_freebrowse_hub_image.py).

The graphics acceptance profile also runs
[`test_freebrowse_image.py`](../tests/container/test_freebrowse_image.py).
It double-clicks a generated volume with special characters in its filename,
checks the default factory opens an embedded FreeBrowse tab without a popup, and checks rendered
image pixels under `/` and `/user/alice/`.
