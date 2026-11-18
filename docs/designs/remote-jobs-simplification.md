---
title: Remote jobs simplification
description: Why `neurodesk jobs` replaced its 12-field site profile and
  cluster-side DataLad with one SSH alias and a standard-library helper
parent: index.md
status: implemented
last-reviewed: "2026-11-18"
---

# Remote jobs simplification

Snapshot: 18 November 2026. [Remote compute jobs](../architecture/remote-jobs.md)
describes the current behavior.

## Problem

The first pilot needed a hand-edited site profile with 12 fields: two SSH
aliases, three separate cluster roots, a client archive root, a cluster
`PATH`, Python and Apptainer paths, Slurm resources, and a scratch retention
window. The profile was rejected unless every field was present. Every
command, `connect` included, validated the whole profile first, so a single
leftover template value stopped everything:

```text
neurodesk-jobs: Configure an absolute apptainer; templates are not site support
```

## Measurements on Bunya

These were measured over SSH on 18 November 2026.

- One login alias. Every new SSH login asks for an Okta number-matching
  push, so the tool must reuse one authenticated connection for the session.
- `/usr/bin/python3` is 3.9.25, and Git and Slurm are in `/usr/bin`. A default
  Slurm account is set, so `sbatch` needs no `--account` or `--partition`.
- Login nodes have no Apptainer. Compute nodes have `/usr/bin/apptainer` 1.5.3,
  and `APPTAINER_BINDPATH=/scratch,/QRISdata` is set site-wide.
- No module or system package provides DataLad or git-annex. The pilot's
  worker needed both on compute nodes, and its `doctor` needed them on login
  nodes too.
- `/scratch/user/$USER` is shared and writable. `$SCRATCH` is not set.

The pilot therefore could not run on Bunya with any profile.

## Decision

A council of three reviewers (Claude Opus, GPT Sol and Gemini Pro) agreed on
these changes:

- `connect HOST` is the only required configuration. It logs in once, then
  discovers Python, `PATH`, home and scratch on the cluster and saves them.
  Later commands find the saved site, and run commands read the site stored
  in the run itself.
- Slurm options move into the request's optional `slurm` section, with
  defaults. The transfer host, retention window and archive root are no
  longer site settings; `archive --to` chooses a destination.
- Login checks cover only what login nodes run. The worker finds Apptainer
  or Singularity on the compute node and records its version. The worker no
  longer requires login-node and compute-node tool versions to be equal.
- Validation errors name the file, field, value and fix. An expired
  connection error says to reconnect.

Two reviewers chose to keep DataLad on the desktop only. One preferred
installing a pinned DataLad and git-annex environment in the cluster account.
The pilot adopted the desktop-only option:

- The desktop uploads the image and selected inputs to a SHA-256 object store
  on cluster scratch. Identical objects, such as a reused SIF, upload once.
- The worker copies the inputs out of the store, verifies them, runs the
  container, and publishes a checksummed receipt.
- `collect` downloads and verifies the results, then saves them as a local
  DataLad dataset. Archives and offline restoration are unchanged.

A managed cluster environment would have kept git history on the cluster.
It would also have added a per-site dependency to maintain, a long first run,
and an internet requirement at setup. The snapshot, source bundles, image
checksum, per-file checksums, scheduler correlation and verified archive
keep the provenance that matters for collection.

## Kept unchanged

- The request format, apart from the optional `slurm` section.
- The submission claim, so a run is never submitted twice.
- Scheduler correlation through `squeue` and `sacct`.
- Independent attempts.
- Scientific validation.
- Verified archives and offline restoration.

## Validation

From a workstation, the DataLad example (`python:3.12-slim` SIF) ran on
Bunya. It went through `connect`, `prepare`, `submit` (which stages the run
automatically), `status`, `collect`, `archive` and offline `restore`. The
mean was computed on a compute node with Apptainer 1.5.3.
`scripts/verify_remote_jobs.py` passed against the same site with nested
datasets and arguments containing spaces and JSON. This is not the full
[site acceptance checklist](../architecture/remote-jobs.md#site-acceptance).
