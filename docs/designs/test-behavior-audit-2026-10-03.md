---
title: Test behavior audit, 3 October 2026
description: Core workflow coverage, false-positive tests, replacements,
  and remaining acceptance gaps
parent: index.md
status: applied
last-reviewed: "2026-10-03"
---

# Test behavior audit, 3 October 2026

The suite exercises several complete application paths. It does not establish
that every shipped application works for an end user. Checkout tests run on
pull requests. Installed-image tests run in the separate candidate-image
workflows before tag promotion. A green checkout suite alone is not image
acceptance.

The audit examined the checkout and container tiers, shared fixtures, and CI
execution. The agreed test boundaries were shipped commands and generated
files, authenticated HTTP and WebSocket requests, browser actions, and
installed application workflows. This is a behavioral audit, not a measured
line-coverage report or an exhaustive mutation score.

## Existing coverage worth keeping

| Area | Observable result | Evidence |
| --- | --- | --- |
| Scientific workflows | CVMFS/FSL, Nipype, Nextflow, and Snakemake produce the expected NIfTI voxels and affine; missing modules prevent output | `tests/container/test_cvmfs_tools.py`, `test_nipype.py`, `test_nextflow.py`, `test_snakemake.py` |
| Slurm | A submitted batch job finishes successfully and writes its expected output | `tests/container/test_slurm.py` |
| T3 | Installed browser app pairs, opens a WebSocket, reloads, and rejects unauthenticated requests under root and Hub-style prefixes | `tests/container/test_t3_code_web_image.py` |
| Jupyter widgets | Real Firefox notebook execution checks stream output, widget restore, reconnect, and concurrent recovery | `tests/container/test_widget_compatibility_image.py` |
| Notebook tools | Bash cells execute; nbconvert exports a PDF; RISE displays slides | `tests/container/test_bash_kernel.py`, `test_nbconvert.py`, `test_rise_slides_image.py` |
| Workspace links | Rendered file links open inside installed JupyterLab | `tests/container/test_workspace_link_routing_image.py` |
| Proxying | Large payloads traverse TCP and Unix-socket backends with size and digest verification | `tests/container/test_jupyter_server_proxy_limits.py` |
| Startup | Executed scripts preserve workspaces, restore defaults, handle custom Jupyter endpoints, and start deferred services independently | `tests/unit/test_startup_batching.py`, `test_wait_for_jupyter.py` |
| Coding agents | Installed subprocesses complete ACP initialization and session probes, with specific missing-auth expectations | `tests/container/test_coding_agents_installed.py` |

Expected arithmetic computed independently with NumPy is a useful oracle for
FSL output. It does not reuse the FSL implementation. Packaging checks for
installation paths, dependencies, immutable assets, and workflow declarations
also remain useful. Neither category should be deleted just because it
contains literals or reads a file.

## Replacements

| Before | Replacement | Fault checked |
| --- | --- | --- |
| ASTRA authentication inferred from `__wrapped__` | Real Jupyter requests with absent, incorrect, and valid tokens under two base paths | Removing authentication allows anonymous requests and fails the denial cases |
| ASTRA assets compared through a private loader | Authenticated HTTP responses with expected MIME types and frontend exports | Missing or denied assets fail HTTP checks |
| ASTRA renderer behavior inferred from source substrings | Execute shipped ESM in jsdom; click modes, expand decisions, select nodes, inspect warnings, retain scroll, and dispose shared-model views | Independent mutations break filtering, ordering, scroll, visible trust, warnings, and another mounted view |
| Widget input assembled from the patcher's own search constants | Frozen excerpts from the pinned upstream wheel, with source hashes and ranges; invoke the public patch CLI | An incorrect search anchor passes the old synthetic fixture but fails against upstream excerpts |
| Empty JupyterHub response accepted on jq 1.6 | Require exactly one JSON user model before printing a server state | Empty, whitespace-only, and multiple-model responses fail without output |
| Any nonzero nbconvert exit counts as a successful negative Bash test | Execute a calculation and a deliberately missing command; inspect executed cell output for both results | An unavailable kernel fails instead of passing |
| DataLad `--version` counts as functioning | Local create, save, clone, get, drop, and get with exact file-content checks | A `get` that returns success without content fails |
| RDP skips a running xrdp and accepts an initial control frame | Use the startup-provisioned service, require pixels, and launch a command by tunnel keyboard input | Incorrect backend credentials must not produce the command's output file |

The widget fixtures are independent upstream input, not regenerated from the
patcher. The remaining synthetic legacy widget assets test migration packaging
only. Runtime recovery continues to be checked by the installed browser tests.

## Validation

The full checkout suite passed 1,163 tests with Python 3.12 and Node.js 24.

The focused installed-image run passed 14 tests as `jovyan`, covering Bash,
DataLad, ASTRA, VNC, and RDP. It used the existing native amd64 image
`ghcr.io/neurodesk/neurodesktop-test:run-37038894496-1-amd64`
with this checkout's tests mounted read-only. The image revision is
`96e35e24a6ce778f5dd630c9d7aa4c40e8347a09`. This was not a fresh image build,
a full runtime-profile matrix, or arm64 validation.

After review, the RDP test replaced a fixed delay with a wait for newly rendered
frames. That version passed with valid credentials and failed with deliberately
incorrect backend credentials. ASTRA mutation checks used temporary copies of
the subject. No deliberate fault was retained in the repository.

## Remaining acceptance gaps

1. The nightly `notebook_(FSL_bet)_workflow.yml` treats terminal output
   as command success and searches whole notebook JSON, including cell source.
   This can accept echoed commands instead of executed results. It needs command
   completion with an explicit exit status, followed by Contents API retrieval
   of cell outputs and numerical checks on the brain and mask. Its live
   JupyterHub credentials and external dataset are outside this local audit's
   runtime verification.
2. ASTRA's file-browser document plugin still has source-based checks for run
   discovery, refresh, retry, and disposal. The new renderer and HTTP cases
   cover those components independently. They do not replace a browser journey
   that opens `astra.yaml`, selects evidence, saves changes, and refreshes.
3. The widget browser test omits NiiVue cells when WebGL2 is unavailable. A
   designated graphics-capable validation profile should require volume
   rendering rather than accepting the portable widget subset alone.
4. Office associations, VS Code readiness, and imaging-tool packaging do not
   establish an open/edit/save workflow for LibreOffice, VS Code, ITK-SNAP, or
   FSLeyes. These need representative document or image workflows.
5. Runtime-affecting PRs need candidate-image validation in addition to checkout
   CI. Existing image workflows protect release promotion, but they are not
   automatically triggered by every pull request.

Current commands and runtime prerequisites are in [Testing](../testing.md).
The earlier [suite audit](test-suite-audit.md) records the tier split and is
preserved as history.
