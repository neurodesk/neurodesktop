---
title: Test suite audit
description: Audit and cleanup record behind the two-tier test layout; the
  current testing contract is documented in testing.md
parent: index.md
status: applied
last-reviewed: "2026-07-31"
---

# Test suite audit and cleanup proposal

Status: **applied**. The two-tier split, the deletions and the documentation
updates described below are in the tree. Where the implementation diverged from
the original proposal, the "As applied" notes at the end of this document say
so and why. Kept as the record of why the suite is shaped the way it is.

The whole of `tests/` is copied into the image at `/opt/tests/` and executed
with `pytest /opt/tests/` inside a running container, five times per build
(the four `std` profiles plus the HPC Apptainer simulation — see
`.github/workflows/build-neurodesktop.yml`). This document audits which of
those tests actually require the running container, which do not, and which
are redundant.

## Headline numbers

| | tests |
| --- | --- |
| Total collected in `tests/` | ~332 |
| Pass today on a bare checkout, no container, no image | **174** |
| Skip *unconditionally* inside the container (pure dead weight in the image) | **24** |
| Would pass outside the container after a mechanical path/dependency fix | ~55 |
| Genuinely need the running container | ~100 |

Roughly two thirds of the suite is executed five times inside a container for
work that a plain `pytest` on the checkout does in about 30 seconds, and 24 of
those tests do not execute inside the container at all — they only skip.

## How this was measured

`pytest` was run per file against the repository checkout with no image
present, and separately against a directory laid out like `/opt/tests` (test
files with no repository above them and a `Dockerfile` sibling), to see which
path branch each file takes in each environment. Per-file results are quoted
in the disposition table below.

## Findings

### A. Three files are shipped into the image only to skip there

`test_github_workflows.py`, `test_report_job_failure_action.py` and
`test_agentic_maintenance_workflows.py` assert on `.github/workflows/**`,
which is not bundled into the image. Each contains a `_read_repo_file()`
helper whose entire purpose is:

```python
if REPO_ROOT == Path("/opt"):
    pytest.skip("repo-only .github workflow files are not bundled into /opt/tests")
```

So in the container all 24 of these tests skip, 5 times per build — 120 skipped
executions and zero signal. They pass in ~1 s on a checkout.

`test_github_workflows.py::test_repo_only_workflow_checks_skip_in_baked_image_layout`
is a test *of that skip shim* — a test that exists only because the tests are
in the wrong place.

### B. Dockerfile text assertions force the build recipe into the runtime image

`Dockerfile` is installed as `/opt/tests/Dockerfile` (`Dockerfile:882`) purely
so that string-matching tests can run in-container. The tests that need it:

- `test_dockerfile_version_pins.py` (2)
- `test_myst_build_workaround.py` (7)
- `test_myst_rise_build.py` (2)
- `test_node_tar_security.py::test_dockerfile_patches_every_bundled_node_tar_copy` (1)
- `test_desktop_launcher_modes.py::test_dockerfile_installs_firefox_wrapper_and_rewrites_desktop_entry` (1)

None of these observes anything about a running container; a `grep` over the
checkout answers all of them. Removing them from `/opt/tests` lets us drop the
baked `Dockerfile` copy, which also stops shipping the full build recipe
(including every pinned internal URL and build-time argument) inside the
published image.

### C. The largest unit-test files already run outside the container

These pass in full on a bare checkout — no image, no services, no mounts:

| file | passing outside container | what it actually exercises |
| --- | --- | --- |
| `test_opencode_web.py` | 72 of 75 | proxy rewrite functions, path confinement, work-dir seeding — imported as a module |
| `test_desktop_launcher_modes.py` | 15 of 15 | shell wrapper behaviour with fake `firefox`/tmp homes |
| `test_cvmfs_selection.py` | 11 of 11 | `cvmfs_server_select.sh` against local mock HTTP servers |
| `test_opencode_prune_sessions.py` | 11 of 11 | synthetic sqlite database |
| `test_nbi_opencode_sync.py` | 9 of 9 | `nbi_setup.sh` against a mock HTTP server |
| `test_nbi_settings_patch.py` | 7 of 8 | `patch_nbi.py` on fixture text |
| `test_jupyterlmod_modulepath.py` | 4 of 4 | module import + monkeypatch |
| `test_nbi_tour_config.py` | 3 of 3 | JSON file contents |
| `test_generate_jupyter_config.py` | 1 of 2 | module import, `tmp_path` |
| `test_jupyter_page_config.py` | 2 of 2 | module import, `tmp_path` |
| `test_apt_install_retry.py` | 1 of 1 | shell script with stubbed `apt-get` |
| `test_codeserver_extensions.py` | 1 of 1 | text of `jupyterlab_startup.sh` |

They already prefer the installed path and fall back to the repository path,
so they run in both places — the container run is just a duplicate.

### D. A further ~55 tests are container-bound only by accident

These are unit tests in the same style, blocked from running outside the
container by a hardcoded absolute path or a missing dependency, not by any
real need for the runtime:

- `test_coding_agents.py` — 29 of 32 skip with "OpenCode/Codex/Claude wrapper
  not installed in this environment". They drive the wrapper scripts with fake
  binaries and a `tmp_path` home. The sources are `config/agents/{opencode,codex,claude}`
  and the file already has an override hook (`NEURODESKTOP_TEST_OPENCODE_WRAPPER`);
  it just lacks the repo fallback every other file has.
- `test_webapp_wrapper_lifecycle.py` (5) and
  `test_webapp_wrapper_request_streaming.py` (3) — already resolve the repo
  copy first; they fail outside only on `import httpx`.
- `test_startup_performance_fixes.py` (7) — `ensure_ssh_keys.sh`,
  `ensure_rdp_backend.sh`, `restore_home_defaults.sh` and the
  `before_notebook.sh` OLLAMA guard, all driven against `tmp_path` homes, but
  addressed through hardcoded `/opt/neurodesktop/...` constants.
- `test_desktops.py` — about 9 of 22 (`test_init_secrets_*`,
  `test_two_users_get_distinct_random_credentials`,
  `test_live_mapping_has_no_literal_default_password`, the two harness-shape
  tests) are `init_secrets.sh` unit tests over `tmp_path`. The rest genuinely
  need VNC/xrdp/Guacamole.
- `test_office_file_associations.py` — the `update_office_mimeapps.py` logic
  is unit-testable; the `gio mime` assertions are not.

### E. Redundant and weak tests

1. **`test_myst_rise_build.py` duplicates `test_myst_build_workaround.py`.**
   Both parse the same Dockerfile block and assert the same strings
   (`jupyterlab_myst==2.7.0`, `install --frozen-lockfile`, the
   `@jupyter/ydoc` exact add, `npm install` absence). Only
   `test_legacy_mathjax3_frontend_is_not_exposed_to_jupyterlab` is unique.

2. **`test_dockerfile_version_pins.py` is a change-detector.** It asserts eight
   literal version strings that must be hand-edited on every bump; it fails by
   construction when a value changes and cannot catch a defect. Three of the
   eight pins (`NODE_TAR_VERSION`, `OPENCODE_VERSION`, the MyST pin) are
   already asserted by the tests that care about them. Either delete it or
   replace it with the invariant it is reaching for — every `ARG *_VERSION` is
   pinned to an exact value rather than a floating tag.

3. **`test_slurm.py` asserts the same thing three times.**
   `test_slurm_setup_when_enabled` already checks the munge socket and
   `scontrol ping`; `test_munge_socket_exists` and `test_slurmctld_ping` are
   strict subsets.

4. **`test_cvmfs_tools.py` overlaps itself and `test_additional_components.py`.**
   `test_cvmfs_setup_when_enabled` and `TestCvmfsMount::test_cvmfs_neurodesk_mounted`
   make the identical assertion; `test_additional_components::test_cvmfs_mounts`
   covers the same ground again; `test_fsl_module_loads` is subsumed by the
   `_fsl_available()` gate the later tests already apply.

5. **FSL is pulled through CVMFS four separate times per config** —
   `test_cvmfs_tools`, `test_nipype`, `test_nextflow` and `test_snakemake` each
   run `module load fsl` and then `fslmaths` (each with a 180–600 s timeout, and
   on arm64 under QEMU emulation). This is the dominant wall-clock cost of the
   container run and it is paid in every one of the CVMFS-enabled profiles.

6. **`test_nipype.py::test_nipype_nonexistent_module_fails` passes vacuously.**
   It passes on a machine with no lmod, no CVMFS and no nipype, because
   `module load funny-name-tool` returns non-zero when `module` is not a command
   at all. It satisfies the negative-test convention in `docs/testing.md` without
   testing the pipeline. It should assert that the *nipype interface* fails —
   the `test_nextflow.py` negative test is the right model, since it also
   asserts no output file was produced.

7. **`test_datalad.py::test_datalad_download` clones from github.com** inside
   every container test profile. It is network-dependent, tests upstream
   DataLad rather than the image, and duplicates `test_datalad_available`'s
   real signal (DataLad is installed and importable). Recommend dropping the
   clone or gating it behind an opt-in marker.

## Proposed structure

```
tests/
  conftest.py          # shared helpers: resolve_source(), run_cmd(), markers
  unit/                # no container required — runs on a checkout in CI
  container/           # requires the running image; this is what /opt/tests holds
```

- `tests/unit/` is the default target for anything that reads repository
  sources, imports a Python module, or drives a shell script against
  `tmp_path`. It runs on `ubuntu-latest` in a new `lint-and-unit` job, on every
  push and pull request, before the image is built. Failures surface in
  ~30 seconds instead of after a full image build.
- `tests/container/` holds only assertions about the running image, and is the
  only thing copied to `/opt/tests`.
- `conftest.py` centralises the `installed path, else repository path` lookup
  that is currently reimplemented five different ways across the suite
  (`first_existing_path`, `_read_first`, `_dockerfile_path`,
  `_load_module`, `_startup_script_path`).

Dockerfile change:

```diff
-    --mount=type=bind,source=tests,target=/tmp/tests,ro \
-    --mount=type=bind,source=Dockerfile,target=/tmp/Dockerfile,ro \
+    --mount=type=bind,source=tests,target=/tmp/tests,ro \
...
-    && cp -a /tmp/tests /opt/tests \
-    && install -m 0644 /tmp/Dockerfile /opt/tests/Dockerfile \
+    && cp -a /tmp/tests/container /opt/tests \
+    && install -D -m 0644 /tmp/tests/conftest.py /opt/tests/conftest.py \
+    && install -D -m 0644 /tmp/tests/pytest.ini /opt/tests/pytest.ini \
```

`pytest /opt/tests/` in the workflows and in `build_and_run.sh` is unchanged.

## File-by-file disposition

Counts in "outside" are the per-file results of running each file against the
bare checkout.

### Move to `tests/unit/` as-is (already green outside the container)

| file | outside | note |
| --- | --- | --- |
| `test_agentic_maintenance_workflows.py` | 7 pass | drop the `/opt` skip shim |
| `test_github_workflows.py` | 10 pass | drop the skip shim and its meta-test (→ 9) |
| `test_report_job_failure_action.py` | 7 pass | drop the skip shim |
| `test_apt_install_retry.py` | 1 pass | |
| `test_codeserver_extensions.py` | 1 pass | |
| `test_cvmfs_selection.py` | 11 pass | |
| `test_jupyter_page_config.py` | 2 pass | |
| `test_jupyterlmod_modulepath.py` | 4 pass | |
| `test_nbi_opencode_sync.py` | 9 pass | |
| `test_nbi_tour_config.py` | 3 pass | |
| `test_opencode_prune_sessions.py` | 11 pass | |
| `test_myst_build_workaround.py` | 7 pass | absorb the unique MyST/RISE assertion |
| `test_generate_jupyter_config.py` | 1 pass, 1 skip | skip needs `traitlets` in the unit job |

### Split — unit half moves out, image half stays

| file | stays in `container/` | moves to `unit/` |
| --- | --- | --- |
| `test_opencode_web.py` | 3 (pinned bundle, Lmod bash env) | 72 |
| `test_desktop_launcher_modes.py` | installed-path check for the Firefox wrapper | 14 |
| `test_nbi_settings_patch.py` | 1 (labextension glob) | 7 |
| `test_node_tar_security.py` | 2 (bundled tar in the image) | 1 |
| `test_coding_agents.py` | 3 (`command -v claude/codex/opencode`) | 29, after adding the repo fallback |
| `test_desktops.py` | ~13 (VNC, xrdp, Guacamole, tunnels) | ~9 `init_secrets.sh` tests |
| `test_office_file_associations.py` | 9 (`gio mime`, defaults dir) | 2 (`update_office_mimeapps.py`) |
| `test_startup_performance_fixes.py` | — | 7, after parameterising the `/opt` paths |
| `test_webapp_wrapper_lifecycle.py` | — | 5, once `httpx` is in the unit job |
| `test_webapp_wrapper_request_streaming.py` | — | 3, same |

### Stays in `tests/container/` unchanged

`test_additional_components.py`, `test_bash_kernel.py`, `test_crud.py`,
`test_cvmfs_tools.py`, `test_kernel_modulepath.py`, `test_nbconvert.py`,
`test_nextflow.py`, `test_nipype.py`, `test_slurm.py`, `test_snakemake.py`,
`test_startup_modes.py`, `test_datalad.py`.

These assert on mounts, installed kernels, running services, deferred-startup
logs, and CVMFS — the things the container run exists for.

### Delete

| test | reason |
| --- | --- |
| `test_dockerfile_version_pins.py` (whole file, 2) | change-detector; pins duplicated by the tests that use them |
| `test_myst_rise_build.py::test_myst_rise_build_uses_pinned_compatible_release` | duplicate of `test_myst_build_workaround.py` |
| `test_github_workflows.py::test_repo_only_workflow_checks_skip_in_baked_image_layout` | tests the skip shim being removed |
| `test_slurm.py::test_munge_socket_exists` | subset of `test_slurm_setup_when_enabled` |
| `test_slurm.py::test_slurmctld_ping` | subset of `test_slurm_setup_when_enabled` |
| `test_cvmfs_tools.py::TestCvmfsMount::test_cvmfs_neurodesk_mounted` | identical to `test_cvmfs_setup_when_enabled` |
| `test_cvmfs_tools.py::TestFslMaths::test_fsl_module_loads` | subsumed by the `_fsl_available()` gate |
| `test_additional_components.py::test_cvmfs_mounts` | third copy of the CVMFS mount assertion |
| `test_datalad.py::test_datalad_download` | network-dependent, tests upstream DataLad |

### Fix rather than move

- `test_nipype.py::test_nipype_nonexistent_module_fails` — make it assert the
  nipype interface fails and produces no output, following `test_nextflow.py`.
- Consolidate the four FSL round-trips: keep one full `fslmaths` execution in
  `test_cvmfs_tools.py`, and let `test_nipype` / `test_nextflow` /
  `test_snakemake` assert only that their runner reaches `fslmaths` and
  produces output, sharing one loaded module via a session fixture.

## Migration steps

Each step is independently mergeable and leaves the suite green.

1. Add `tests/conftest.py` with the shared source-resolution helper and
   `tests/unit/` + `tests/container/` directories. No test moves yet.
2. Move the group that is already green outside the container into
   `tests/unit/`, deleting the three `/opt` skip shims. Add the
   `lint-and-unit` CI job that runs `pytest tests/unit/`.
3. Point the Dockerfile at `tests/container/` and drop the
   `install -m 0644 /tmp/Dockerfile /opt/tests/Dockerfile` line and its bind
   mount.
4. Split the mixed files (table above). This is the only step that touches
   test bodies; the `/opt`-first, repo-second lookup from `conftest.py`
   replaces each file's private helper.
5. Apply the deletions and the two `Fix rather than move` items.
6. Update `docs/testing.md` and `AGENTS.md`.

## Documentation to update

- `docs/testing.md` — replace "run the tests inside the container under
  `/opt/tests/`" with the two-tier model: `pytest tests/unit/` on a checkout,
  `pytest /opt/tests/` in the built image. Delete the paragraph describing the
  baked `/opt/tests/Dockerfile`, and the "focused CI workflow source-shape
  checks" section, which becomes the default. Tighten the Negative Test
  Convention so a negative test must assert absence of output, not just a
  non-zero exit code (finding E6).
- `AGENTS.md` — the four rules naming specific test files need new paths.
  `test_report_job_failure_action.py` and
  `test_agentic_maintenance_workflows.py` move under `tests/unit/`; the MyST
  and NBI rules split across both tiers; the OpenCode rule keeps its
  "in the built image" wording only for the real-bundle contract test, which
  is one of the three staying in `tests/container/`.

## As applied

The migration landed in one change rather than the six staged steps, since the
steps only made sense as separate pull requests and the split had to be
consistent to keep both tiers green. Outcome:

- `tests/unit/` — **212 tests, all passing on a bare checkout with no image**,
  no skips. Run by the new `Unit tests` workflow
  (`.github/workflows/unit-tests.yml`) on every push and pull request, in about
  a minute.
- `tests/container/` — 109 tests collected, installed at `/opt/tests`.
- `tests/testlib.py` + `tests/conftest.py` — the shared resolution helpers,
  installed alongside the container tier so both layouts use the same lookup.
- The `Dockerfile` no longer bakes itself into the image as
  `/opt/tests/Dockerfile`, and copies only `tests/container/`.

### Divergences from the proposal

Three items were re-decided once the code was in front of us:

1. **`test_desktops.py` stays wholly in the container tier.** The proposal
   expected ~9 `init_secrets.sh` tests to move. They cannot:
   `config/guacamole/init_secrets.sh` hard-codes
   `/etc/guacamole/user-mapping-*.xml` and shells out to `vncpasswd`, with no
   environment override for either. Moving those tests would have required
   changing production code to add test seams, which is a larger and riskier
   change than this refactor. Left as a known follow-up.

2. **`test_office_file_associations.py` stays wholly in the container tier.**
   The proposal counted 2 movable tests, on the evidence that
   `test_xarchiver_no_longer_claims_office_documents` passes outside the image.
   It passes *vacuously* — nothing is registered on a bare checkout, so
   "xarchiver is not offered" is trivially true. Both are runtime assertions.
   `config/lxde/update_office_mimeapps.py` has no unit test at all; that is a
   coverage gap, not something to relocate.

3. **`test_coding_agents.py` gained coverage rather than just moving.** 29 of
   its 32 tests were skipping everywhere except inside the image. Pointing the
   wrapper lookup at `config/agents/{opencode,codex,claude}` makes all 29 run —
   they now execute on every push instead of only in the container matrix.

### Deletions applied

`test_dockerfile_version_pins.py` (2) and `test_myst_rise_build.py` (2, with its
one unique mathjax3 assertion absorbed into `test_myst_build_workaround.py`)
were removed as whole files, along with
`test_github_workflows.py::test_repo_only_workflow_checks_skip_in_baked_image_layout`,
`test_slurm.py::{test_munge_socket_exists,test_slurmctld_ping}`,
`test_cvmfs_tools.py::{TestCvmfsMount::test_cvmfs_neurodesk_mounted,TestFslMaths::test_fsl_module_loads}`,
`test_additional_components.py::test_cvmfs_mounts` and
`test_datalad.py::test_datalad_download`.

One deletion was added beyond the proposal:
`test_cvmfs_tools.py::TestFslMaths::test_fslmaths_runs` is a strict subset of
`test_fslmaths_arithmetic`, which runs `fslmaths`, checks the output file *and*
verifies the arithmetic. Dropping it removes a second full FSL container pull
(600 s timeout, QEMU-emulated on arm64) from every CVMFS-enabled profile.

The cross-file FSL consolidation sketched under "Fix rather than move" was not
attempted: `nextflow` and `snakemake` load the module inside their own job
scripts, in separate processes, so a session fixture cannot share it — the only
real saving was the duplicate `fslmaths` run above, which is now gone.

### Verification

`pytest tests/unit` was run on the checkout (212 passed). The container tier was
verified by collection only, against a directory laid out like `/opt/tests` —
109 tests collect and `testlib` resolves correctly in that layout. It has **not**
been executed inside a built image: no Docker daemon was available in the
environment this change was made in. The first build workflow run is the real
check on the container tier and on the `Dockerfile` edit.
