---
title: Build-time behaviors
description: Image build steps with non-obvious behavior — the Notebook
  Intelligence patches and rebuild, the MyST/RISE rebuild, the Apptainer build
  stage, and user permissions
parent: ../architecture.md
status: current
last-reviewed: "2026-10-20"
---

# Build-Time Behaviors

Part of [Architecture](../architecture.md). Build arguments are listed in
[Environment variables](../environment-variables.md#build-arguments). Two
further build-time behaviors live with their subsystems:
[config generation](webapps.md#build-time-config-generation) and
[CVMFS setup](cvmfs.md#build-time-cvmfs-setup).

## Publication

The [image release workflows](../testing.md#image-release-validation) build
candidates for a specific run and attempt, test and scan them, and then promote
the same candidates to release tags. Native runtime checks cover both image
architectures. A date tag never substitutes for building the run's source SHA.

The shared [Trivy policy](../../.trivyignore.yaml) records individual scan
exceptions. [Issues #1142](https://github.com/neurodesk/neurodesktop/issues/1142)
and [#1152](https://github.com/neurodesk/neurodesktop/issues/1152) identified
twelve kernel CVEs reported against Ubuntu 26.04's
`linux-libc-dev` 7.0.0-38.38. That package contains UAPI headers without the
affected kernel implementation. CVMFS requires it through `uuid-dev` and
`libc6-dev`; removing the headers breaks that dependency chain. Containers use
the host kernel, whose security updates remain the host administrator's responsibility.
These twelve exceptions match only the assessed Ubuntu package and version,
on either architecture, and expire on 2026-11-20 for reassessment. Other
package versions, kernel packages, and CVEs remain subject to the scan gate.

## Base image dependencies

The base image uses Ubuntu 26.04. Guacamole builds against FreeRDP 3 and ships
the matching FreeRDP 3 client libraries. Its build selects GNU C17 because
[GCC 15 defaults to C23](https://gcc.gnu.org/gcc-15/porting_to.html).
It accepts deprecated FreeRDP API declarations and requires the RDP plugin
to exist after installation.

The build backports [GUACAMOLE-2328](https://issues.apache.org/jira/browse/GUACAMOLE-2328)
from upstream commit
[`ab36756b596520ae2a94cd4d25e406e0b5aa820b`](https://github.com/apache/guacamole-server/commit/ab36756b596520ae2a94cd4d25e406e0b5aa820b).
The hash-checked [patch](../../config/guacamole/guacamole-1.6.0-GUACAMOLE-2328.patch)
moves GDI initialization and its dependent rendering handlers to PostConnect,
after FreeRDP creates the connection cache. This prevents the RDP worker crash
with FreeRDP 3.31 and later. The 1.6.0 backport preserves its existing RAIL and
static-channel loading in PreConnect. The installed RDP test requires desktop
pixels and a command executed through the authenticated Guacamole tunnel.

The generated Guacamole header drops its fixed `_XOPEN_SOURCE` definition;
its `_GNU_SOURCE` definition lets libc choose the matching feature level.
The bounded substring helper casts `memchr`'s result to preserve Guacamole's
existing mutable-pointer return type with libc's const-aware declaration.
The guaclog key lookup and copy helpers retain const pointers to their static
key table before producing an owned copy.
CVMFS uses the release packages built for Ubuntu 26.04.

## Bundled code-server dependencies

The builder replaces VS Code's nested `shell-quote` package at
`/opt/code-server/lib/vscode/node_modules/shell-quote` with the
`SHELL_QUOTE_VERSION` release. The package has no runtime dependencies.
It also installs `PROXY_ADDR_VERSION` into code-server's npm dependency tree,
updating the manifest and shrinkwrap together. Both installed versions are
checked before code-server is copied into the runtime image.

The [version audit](../../scripts/audit_image_versions.py) reads both pins
from the Dockerfile and checks releases against npm. The
[installed-package tests](../testing.md#bundled-code-server-security-regressions)
exercise quoting and proxy trust behavior at the shipped paths.

## Layer Ordering and Cache

The pip install layer runs
[`sanitize_ghapi_examples.py`](../../scripts/sanitize_ghapi_examples.py) to replace
five public credential-revocation examples in `ghapi/gh_spec.py` with descriptive
placeholders. These are the sample values published in
[GitHub's API documentation](https://docs.github.com/en/rest/credentials/revoke),
which otherwise trigger Trivy secret findings in the source and its compiled
cache. The cleanup matches only those exact examples, preserves API metadata,
and removes only `gh_spec.*.pyc` cache files before the install layer is committed.
Unexpected example changes fail the build for reassessment. Secret-scanning
rules and release gates remain unchanged.

The same pip layer runs
[`sanitize_anthropic_examples.py`](../../scripts/sanitize_anthropic_examples.py)
against six Anthropic SDK modules. Trivy identifies the public JSON example
`{"type": "service_account"}` as a Google credential even without a key.
The cleanup rewrites that example as `dict(type="service_account")` only in
standalone documentation strings and removes the affected modules' bytecode.
It validates all six files before writing and fails if their example counts
change or the literal appears in executable code. SDK request construction,
user credentials, and scanner rules remain unchanged.

The main Python environment pins `pip` 26.2.2, `certifi` 2026.10.19, and
`uv` 0.13.4. Their fixed releases remove the compromised emSign G3 trust
paths. Updating the main `certifi` distribution does not update pip's vendored
copy, so pip is upgraded before installing the other Python packages.
The installed-image test inspects both certifi bundles. The environment also
pins Brotli 1.2.1 so malformed compressed input cannot reach the decoder
out-of-bounds write in 1.2.0 (CVE-2026-111497). The build reads these
security-sensitive packages back from installed metadata, and the image test
exercises Brotli compression and decompression. The pins remain under the
direct-version audit and Renovate tracking.

The runtime stage is ordered by how often each layer's inputs change, because
invalidating a layer re-runs every layer after it. Three bands, in order:

1. **Stable system software** — apt packages, builder-stage copies, Tomcat,
   TinyTeX, Firefox, conda/pip, and the three expensive from-source
   labextension rebuilds. Keyed on pinned versions that rarely move.
2. **Pinned-version tool installs** — CVMFS keys, neurocommand, the agent
   CLIs (codex, claude, opencode), the ACP adapters, and lightcone. Keyed
   only on explicit version/ref bumps, never on local files.
3. **Local-file layers** — the local JupyterLab extension builds
   (`extensions/`), then kernel/Guacamole/home-default configuration, and
   finally the catch-all runtime-config layer. Everything here is keyed on
   repository files that change frequently, so it sits last and re-runs
   cheaply.

Layers that consume repository files bind-mount the specific files they
install rather than whole directories: a whole-directory mount keys the layer
on every sibling file, so an unrelated edit (for example a unit test, when
all of `tests/` was mounted) would needlessly rebuild everything downstream.
When adding a layer, mount individual files and place the layer at the
band matching its most volatile input.

The runtime stage starts from the pinned multi-architecture
`jupyter/base-notebook` date tag and runs an apt `dist-upgrade` against fresh
Ubuntu metadata before installing Neurodesktop's runtime packages. The pip
layer upgrades every direct requested distribution while retaining explicit
compatibility holds. Resolver-selected dependencies inherit the new base and
fresh direct solves; the build does not run an unbounded post-solve pip or
conda upgrade that could replace the base image's tested Jupyter environment.
Snakemake 9.27.0 has an isolated tool environment because its `packaging<26`
requirement conflicts with the base image's Conda requirement of
`packaging>=26.3`. Its private environment uses `packaging` 25.0 and exposes the
normal `snakemake` command through `/usr/local/bin`. The main environment
retains Conda's compatible release. Lightcone has its own isolated environment
and does not install Snakemake.

The ipyniivue patch is one deliberate exception: its version-coupled source is
mounted into the pip layer because that layer must replace the installed 5 MB
bundle before it is committed. Applying the patch in the later local-file band
would leave the unused package copy in image history.

The pip layer also builds `jupyterlab-slurm` 4.1.0 from an exact source
revision. Upstream now uses `@jupyter/builder` directly. The build checks its
declared builder range against the image's builder pin before producing the
wheel; the previous package-rename workaround is no longer needed.

Both Jupyter source builds use [build constraints](../../config/jupyter/build-constraints.txt)
for the isolated Python build environment. The toolchain pins Hatchling 1.32.4
and hatch-jupyter-builder 0.10.0. The launcher also declares that pair in its
own build metadata. Use pip's `--build-constraint`, not `-c`: ordinary runtime
constraints do not constrain isolated build dependencies. Changes to these
pins require fresh isolated wheel builds for both `jupyterlab-slurm` and
`neurodesk-launcher`.

The pinned FreeBrowse extension compiles its TypeScript and builds its federated
bundle with `jupyter-builder` and the installed core metadata at
`/opt/conda/share/jupyter/lab/static/package.json`. Its upstream production script
omits that core path. With the current Jupyter builder, the legacy builder
declaration otherwise triggers a lookup for unavailable 4.0 core metadata.
Using the installed core keeps the bundle aligned with the image's JupyterLab
and avoids that network lookup.

## Image Size Hygiene

Layers are append-only: deleting or re-owning a file in a later layer only
adds whiteouts or duplicates while the original layer keeps shipping the
bytes. Checkout tests in `tests/unit/test_image_packaging_layers.py` check
that cleanup happens before each layer ends. Built-image tests in
`tests/container/test_image_size_hygiene.py` check the remaining runtime assets.
The image follows these rules:

- **Build-only packages are purged in the layer that needs them.** The pip
  layer runs as root, installs `build-essential` (gcc for sdist-only
  packages such as `traits`, which ships no cp313 wheel), runs the pip steps
  as `${NB_USER}` via `runuser`, and purges the toolchain before the layer
  ends. The same layer installs and purges `libgpgme-dev` and
  `libossp-uuid-dev`. Node's unused C headers are deleted in the nodejs
  install layer.
- **Ownership and modes are set where a tree is created.** `/usr/local/tomcat`
  is chowned and made world-readable in its install layer and in the WAR
  extraction layer; a whole-tree `chown -R` in a later layer would duplicate
  ~50 MB per run.
- **Unreachable payload is stripped in the layer that installs it.** Vendored
  duplicate agent binaries (the ACP adapters' platform packages and
  `claude-agent-sdk/_bundled`, together ~750 MB), webpack/TS sourcemaps, a
  curated list of heavyweight bundled Python test suites, and Tomcat's
  default webapps are all deleted where they first appear. ipyniivue's 5 MB
  frontend is moved from its per-model anywidget trait into JupyterLab's static
  tree in its pip install layer, so the replaced package copy does not remain
  in image history.

The pip layer removes the original MyST, collaboration, and document-provider
frontend bundles before the later source builds replace them. NBI and MyST each
expose one package bundle through a symlink in Jupyter's extension
directory. Guacamole's WAR is downloaded, migrated, extracted, patched, and
deleted in one layer. Apptainer's unused CNI plugins are removed in its builder
before the runtime tree is copied.

## ipyniivue Frontend Packaging

ipyniivue 2.4.4 declares its bundled ESM with a Python ``Path``. anywidget
turns that file into a synchronized string on each widget model, so a notebook
with nine viewers sends and imports about 45 MB of repeated JavaScript. The pip
layer runs an anchored patch that publishes the large module once under a
content-derived name in JupyterLab's static directory and replaces the package
file with a small same-origin loader.

The large module originally keeps its NiiVue instance in module globals. A
normal fixed-URL import would therefore make every widget share one viewer.
The patch wraps those globals in an exported definition factory, and the small
per-model loader calls the factory after importing the cached module. The
factory replaces ipyniivue's permanent 30 ms scene polling with one
asynchronous scene-delta send for each focused ``NiiVue.sync()`` event. Its
model-destruction cleanup also releases the NiiVue WebGL context. The existing
view cleanup remains non-destructive so a view can move between output areas.
The patch is tied to the exact 2.4.4 bundle seams and fails the build when a
package upgrade changes them.

## Notebook Intelligence Patches

The upstream Notebook Intelligence settings panel auto-saves its client-side
state on open, using the capabilities cache fetched at page load. That
reverts any `~/.jupyter/nbi/config.json` change made behind the server's
back — in particular the OpenCode model selection mirrored by
`nbi_setup.sh`. Until this is fixed upstream, the Dockerfile pins
`notebook_intelligence` and runs
[`config/agents/patch_nbi.py`](../../config/agents/patch_nbi.py) to rewrite the
bundled labextension so opening the settings panel first re-fetches
capabilities (the backend reloads the config file from disk to answer) and
rebuilds the panel from that fresh state.

The same patcher also fixes the server-side Ollama provider: `ollama list`
reports an empty `details.family` for models imported from safetensors/mlx,
so upstream's `f"{family}.context_length"` lookup raises and the provider
logs an ERROR and drops the model from the chat model list. The patch falls
back to the model info's sole `*.context_length` key; a model without any
such key is still skipped as before.

Both patches are anchored on the exact upstream code and fail the image
build when a `notebook_intelligence` upgrade changes it, so the workarounds
cannot silently regress; re-verify and update (or drop) them when bumping
the pin.

Notebook Intelligence 6.2.0's published frontend accepts the image's JupyterLab
4.6 launcher. The image uses that bundle directly and checks that a
`remoteEntry` exists before applying the patches. The settings patch preserves
the optional tab selection after refreshing capabilities. The Ollama patch
matches the lookup's exact nesting inside the provider's bounded client.
The obsolete 5.3.1 source rebuild and its Yarn lockfile are removed.

## MyST and RISE Extension Build

MyST is rebuilt against RISE's JupyterLab application so its markdown viewer is
available in presentation mode. MyST 2.7.0's published shared-package metadata
requests Jupyter YDoc 3.x, while the base image's JupyterLab 4.6 uses YDoc 4.x;
the source build pins that exact YDoc 4 release in both the package manifest and
the generated lockfile. RISE also retains
a Python dependency on the legacy `jupyterlab-mathjax3` package. Its JupyterLab
3-only frontend is not exposed in the final application; JupyterLab 4.6 and
RISE's standalone application both provide the current built-in MathJax
extension.

The standalone RISE application does not expose the full set of services in
JupyterLab. Neurodesktop therefore filters its page config to the
`jupyterlab-rise` extension and the MyST bundle rebuilt against that app. This
keeps collaboration, chat, file-browser, and widget extensions from disabling
RISE's built-in notebook cell executor or requesting services the standalone
app does not provide. Full JupyterLab still loads those extensions normally.
The filter is an anchored patch to `jupyterlab_rise.app`; a changed upstream
anchor fails the image build and requires the patch to be reassessed.

## Apptainer

The Dockerfile builds Apptainer from upstream source in a dedicated build stage
on the runtime's Ubuntu base and copies `/opt/apptainer` into the runtime image.
It copies the pinned Go SDK from the official Go image. Matching the runtime
distribution keeps native dependencies such as `libsubid` on the same ABI;
the runtime layer executes `apptainer --version` to catch missing libraries.
The build is controlled by
`APPTAINER_VERSION`, `APPTAINER_GO_VERSION`, `APPTAINER_GRPC_VERSION`, and
`APPTAINER_CRYPTO_VERSION` so the image can move to scanner-fixed Go toolchain
and module versions before a matching upstream multi-arch runtime image is
published. The crypto override is applied to both Apptainer and its separately
vendored gocryptfs build.

macOS Docker/root sessions use `--overlay /tmp/apptainer_overlay` for writable
container sessions. This works around the "FATAL:   image targets 'amd64',
cannot run on 'arm64'" bug on macOS. Other non-Apptainer sessions leave
`neurodesk_singularity_opts` empty because it interferes with VS Code and
Matlab. Non-root Apptainer/HPC sessions use `--writable-tmpfs` because setuid
Apptainer cannot use a directory overlay as an unprivileged user.

Root startup adds missing CPU MHz information for Matlab by binding a prepared
file onto `/proc/cpuinfo`. The successful bind source stays in the root-private
`/run/neurodesktop/cpuinfo` directory for the mount's lifetime. Unlinking that
source prevents nested Apptainer sessions from binding their own CPU information
onto `/proc/cpuinfo`. Failed preparation removes its temporary file; repeated
startup leaves an existing CPU MHz entry intact.

When `/proc/driver/nvidia/version` shows that the proprietary NVIDIA driver is
loaded, `environment_variables.sh` defaults `APPTAINER_NV` to `1`. Apptainer
then binds the host's NVIDIA device files and userspace libraries into each
tool container. The libraries support both CUDA and an NVIDIA-driven display.
An NVIDIA-driven display needs the host's `libGLX_nvidia.so.0` even for an
OpenGL application that does not use CUDA. An explicit `APPTAINER_NV` value
takes precedence, so `APPTAINER_NV=0` disables the binds when a host library is
incompatible with an older container glibc.

## User Permissions

The container runs as the `jovyan` user from the base Jupyter image. The
`NB_UID` and `NB_GID` environment variables allow matching host user
permissions.
