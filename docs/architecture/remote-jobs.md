---
title: Remote compute jobs
description: Experimental CLI that runs a DataLad-prepared Apptainer job on a Slurm cluster over SSH
parent: ../architecture.md
status: current
last-reviewed: "2026-11-18"
---

# Remote compute jobs

`neurodesk jobs` runs one containerized analysis on a user's Slurm account
through SSH. DataLad stays on the desktop. The cluster needs only:

- an SSH login;
- Python 3.9 or newer on the login PATH;
- Slurm with `sacct` accounting;
- Apptainer or Singularity on the compute nodes;
- shared home and scratch storage.

It does not need DataLad, git-annex, modules, CVMFS, Globus, or a resident
agent. Jobs run and publish while Neurodesktop is closed. Collection and
archiving happen when the desktop reconnects. The
[design record](../designs/remote-jobs-simplification.md) explains why it
works this way. This is a pilot; see [site acceptance](#site-acceptance).

## Connect

```bash
neurodesk jobs connect bunya
```

The host is an alias from the user's `~/.ssh/config`. `connect` opens one
OpenSSH master connection. That login is the only one that may prompt,
including for site MFA such as Bunya's Okta push. The master's private socket
is under `~/.ssh/neurodesk-jobs`, and it persists for 4 hours. Every later
command reuses it with `BatchMode=yes`, connection timeouts and keepalive
limits. An expired connection fails with an instruction to run `connect`
again. No SSH agent is forwarded, and the tool never stores passwords or keys.

On first use, `connect` discovers these values with one SSH command and saves
them to `STATE/site.json`:

| Field | Discovered value |
| --- | --- |
| `python` | First `python3` (or `python3.X`, 3.9 or newer) on the login PATH |
| `path` | The login PATH, used for the job script, which runs with `--export=NONE` |
| `state_root` | `$HOME/.neurodesk-jobs`: persistent journals, helpers, logs and receipts |
| `data_root` | `neurodesk-jobs` under the first writable `$SCRATCH`, `/scratch/user/$USER`, `/scratch/users/$USER` or `/scratch/$USER` |

`--data-root PATH` overrides scratch detection. If no scratch directory is
found, the error names that option. Running `connect` again with no host
reuses the saved site. Naming a different host rediscovers and replaces the
saved site, but runs keep the site they were prepared with. `connect` then
installs the helper and reports the login checks. `doctor` repeats those
checks, which cover Python, `sbatch`, `squeue`, `sacct` and writable roots.
They do not require Apptainer on login nodes.

`STATE` defaults to `~/.local/state/neurodesk-jobs`; `--state` changes it.
Keep it on persistent storage. Only one desktop process at a time changes a
state directory.

## Prepare and submit

Start with a saved, clean DataLad dataset, including its installed
subdatasets, and an Apptainer SIF compatible with the cluster. The request
lists:

- the exact input files;
- the command and validation argument vectors;
- the required nonempty outputs;
- optional Slurm options.

```json
{
  "inputs": ["subject/anat.nii.gz", "analyze.py", "validate.py"],
  "command": ["python", "/inputs/analyze.py", "/inputs/subject/anat.nii.gz", "/outputs/summary.json"],
  "validate": ["python", "/inputs/validate.py", "/outputs/summary.json"],
  "outputs": ["summary.json"],
  "slurm": {"time": "02:00:00", "mem": "16G", "cpus-per-task": 4}
}
```

Slurm defaults are 1 hour, 4 GB and 1 CPU. `slurm` may also set `account`,
`partition`, `qos` and `gres`. When those are omitted, Slurm's own defaults
apply. Directories, globs and ordinary symlinks are not inputs; annexed files
are. Commands are argument vectors, not shell snippets. The output names
`execution.json`, `outcome.json`, `worker.log`, `.gitattributes` and
`.gitmodules` are reserved.

```bash
neurodesk jobs prepare --dataset DATASET --image tool.sif --request request.json
neurodesk jobs submit RUN_ID      # uploads the run first if needed
neurodesk jobs status RUN_ID
neurodesk jobs logs RUN_ID [--attempt ATTEMPT_ID]
neurodesk jobs cancel RUN_ID
```

`prepare` builds a local DataLad snapshot under `STATE/runs/RUN_ID/input`. It
holds the selected input bytes in their relative layout, a Git bundle for each
source dataset, the SIF, and a manifest. The manifest records dataset IDs,
commits, commands and SHA-256 checksums. Workers never follow `.gitmodules` or
upstream URLs. Each run is its own snapshot.

`stage`, which `submit` runs automatically, uploads only the SIF and the
selected inputs. They go into a content-addressed store at
`data_root/objects/<sha[:2]>/<sha256>`. The helper checks each object's size
and SHA-256 before publishing it read-only, and skips objects that are already
present. A reused image therefore uploads once. Source bundles stay on the
desktop.

## Worker

The job script runs an immutable, version-addressed helper zipapp from
`state_root/helpers`. Each worker start, including a forced restart, creates
an exclusive attempt journal and a workspace under `data_root/work/ATTEMPT`.
The worker:

1. finds `apptainer`, or else `singularity`, on the compute node's PATH;
2. copies inputs out of the store and verifies every checksum, including the
   image's; missing or corrupt content is `inputs-missing`, and the analysis
   does not start;
3. runs `exec --containall --cleanenv --no-home` with `/inputs` read-only and
   `/outputs` read-write, after removing the `APPTAINER_BIND*` and
   `SINGULARITY_BIND*` variables, so binds set through the environment, such
   as Bunya's `/scratch`, are not added; binds in the site's `apptainer.conf`
   still apply;
4. requires the listed outputs to be nonempty, then runs validation with
   `/outputs` read-only;
5. requires every output to be a regular file with a safe relative path and
   no Git metadata, so that `collect` can commit it;
6. writes `execution.json` (manifest, compute host, tool versions and command),
   `outcome.json` and `worker.log`, makes the outputs read-only, and records a
   receipt of every output's SHA-256.

The job script uses the login PATH saved by `connect`. On a site that
provides Apptainer only through `module load`, the worker reports that no
runtime was found. Add the module's `bin` directory to `path` in
`STATE/site.json`, then prepare a new run, which keeps a copy of the site.

The container is not a network sandbox. A site's offline test must show that
the analysis has no hidden dependency.

## Recovery and result states

Before claiming a run, the helper calls `sbatch --test-only` with the same
options. Slurm rejects an invalid partition, account or resource request at
this point, before anything is submitted. The error tells the user to fix the
request's `slurm` section and prepare a new run. The helper then atomically
claims the run and flushes the submission intent, and calls
`sbatch --parsable --no-requeue` once. A permanent claim prevents a
second submission, including after a helper failure. If the response is
lost, the state stays `submission-unknown`, and `submit` exits with an error
that says so. Repeating `submit` returns the existing record and does not call
`sbatch` again.

`status` correlates the owner, run name and scheduler comment in `squeue` and
`sacct`. Accounting must keep job comments to recover a lost job ID. No
matches, unavailable accounting, conflicting matches and a missing journal
all remain uncertain. Resolve those cases with scheduler evidence. A new
`prepare` creates a distinct run and may duplicate accepted work.

| Evidence | Meaning |
| --- | --- |
| Accepted job ID | Slurm accepted the request; computation has not necessarily started |
| Attempt `inputs-missing` | Staged content was missing or corrupt; analysis did not start |
| Attempt `failed` | The runtime was missing, or analysis, validation or publication failed; see `error` and `logs` |
| Attempt `running` | Inputs verified; analysis started |
| Attempt `site-results-available` and receipt | Analysis and validation passed; outputs are checksummed on scratch |
| Slurm `COMPLETED`, exit `0:0`, valid receipt | Eligible for collection |
| `collection.json` | The client downloaded, verified and committed one canonical result |
| `archive.json` | A self-contained archive was independently restored and verified |

Timeout, OOM, cancellation, a killed worker or an unknown scheduler state
cannot be overridden by a receipt. Workspaces are kept after failures.

## Collect, archive and restore

```bash
neurodesk jobs collect RUN_ID
neurodesk jobs archive RUN_ID [--to DIRECTORY]
neurodesk jobs restore ARCHIVE.tar.gz DESTINATION
```

`collect` downloads every receipt file through the helper and checks each
against its SHA-256. It then saves the files as a new local DataLad dataset
at `STATE/runs/RUN_ID/output`. If several attempts qualify, it chooses the
first eligible verified attempt and reports the others without merging them.

`archive` writes a tarball under `STATE/archives`, or under `--to`. The
tarball holds Git bundles and the plain bytes of the input snapshot and the
output dataset, together with the run, collection and status records. The
command restores the tarball into a new directory and checks commits,
identities and hashes before issuing a receipt. It refuses to overwrite an
existing archive.

`restore` needs DataLad, Git and git-annex, but no cluster, credentials or
scratch store. It rejects links and path traversal. Choose an archive
destination whose retention and backup policy suits the project. Never put
active DataLad work on QRISdata.

Scratch copies are not preservation copies. The tool never drops data or
cleans up attempts automatically. Collect results before the site purges
scratch.

## Site acceptance

Bunya passed the DataLad example and `scripts/verify_remote_jobs.py` on
18 November 2026 (see the design record). Before research use on any site,
run these checks with synthetic or public data:

1. One interactive login reused through submit and collect; expiry and
   reconnection.
2. Concurrent submissions from two login nodes, with no duplicate jobs.
3. A lost `sbatch` response and delayed accounting.
4. Queued and running cancellation, timeout, OOM, a full disk and a failed
   validation, with no false success.
5. Desktop shutdown after submission, then collection.
6. Archive restoration after scratch is unavailable, with confirmed storage
   permissions, retention and RDM policy.
7. The same analysis on a second site, within predetermined scientific
   tolerances.

## Development

The implementation is in [`config/remote_jobs`](../../config/remote_jobs/).
Run it from a checkout with
`PYTHONPATH=config/remote_jobs python -m neurodesk_jobs.cli ...`. The cluster
helper must remain standard-library Python 3.9. `remote.py` is the cluster
side, and DataLad is used only in `data.py` and `client.py` on the desktop.
See [testing](../testing.md#remote-compute-jobs).
