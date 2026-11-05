---
title: Remote compute jobs
description: Experimental CLI for DataLad snapshots, SSH/Slurm jobs, and verified archives
parent: ../architecture.md
status: current
last-reviewed: "2026-11-05"
---

# Remote compute jobs

`neurodesk-jobs` submits one containerized analysis to a user's Slurm account
through SSH. DataLad/git-annex transports the inputs and results through a RIA
directory store. Jobs execute and publish independently of Neurodesktop.
Collection and archiving require the client to reconnect. No Globus service,
Forgejo runner, resident agent, or web UI is involved.

This is a pilot. **Bunya and Sherlock profiles are templates, not validated
site integrations.** Configure the actual allocation, paths and tools, and
complete the site acceptance checks below before using research data.
The existing local Neurodesktop Slurm service and remote desktop connection
scripts are separate features.

## Configure a site

Copy `/opt/neurodesktop/remote-jobs/profiles/bunya.json` or `sherlock.json`
to persistent project storage. In a source checkout these files are under
[`config/remote_jobs/profiles`](../../config/remote_jobs/profiles/).
Replace all placeholders. The CLI rejects incomplete templates.

| Field | Meaning |
| --- | --- |
| `host`, `transfer_host` | OpenSSH aliases configured by the user; they may be identical |
| `state_root` | Private, **shared persistent** cluster directory for submission journals, helpers, logs and receipts; never node-local `/tmp` or purgeable scratch |
| `store_root` | Private cluster RIA store, accessible at the same absolute path from command host, transfer host and compute nodes |
| `work_root` | Private cluster workspaces, accessible to compute nodes; separate from the store and journal |
| `archive_root` | Explicitly nominated persistent directory **mounted on the client**, with sufficient capacity for inputs, software and results |
| `path`, `python`, `apptainer` | Cluster tool locations, including Python ≥3.10, DataLad, git-annex, Git, Slurm and Apptainer; no assumption of modules or CVMFS |
| `resources` | `time`, `mem`, `cpus-per-task`; optional `account`, `partition`, `qos`, `gres` |
| `scratch_retention_days` | Confirm the applicable retention window with the site; this is a warning, not a retention guarantee |

Use a filesystem whose atomic directory creation and replacement work across
login nodes for `state_root`. The user must confirm its storage policy; a
directory existing does not establish persistence or backup. Keep the client
`--state` directory on a persistent project mount too. One desktop process at a
time owns changes to that directory; sharing it among independent desktops is
outside this pilot.

Set up the SSH aliases using the site's current connection instructions:
[Sherlock connection guidance](https://www.sherlock.stanford.edu/docs/advanced-topics/connection/)
and [UQ RCC documentation](https://docs.hpc.rcc.uq.edu.au/).
Do not copy a private key or password into the profile. SSH handles interactive
site authentication, including MFA. The CLI does not install authorized keys
or alter site authentication policy.

```bash
neurodesk-jobs --profile /project/bunya.json --state /project/job-state connect
neurodesk-jobs --profile /project/bunya.json --state /project/job-state doctor
```

`connect` opens authenticated OpenSSH masters with private sockets under
`~/.ssh/neurodesk-jobs`. Git, DataLad, annex and ORA reuse those connections
through private SSH/SCP wrappers. Subsequent operations use `BatchMode=yes`,
connection timeouts and keepalive limits. An expired connection causes an
error; run `connect` again in a terminal. No SSH agent is forwarded to jobs.
Job subprocesses request early loading of the optional `next` extension. When
installed, loading it before core RIA imports avoids mixing old and patched
transport classes. DataLad without that extension remains usable. This does
not change the user's DataLad configuration for other work.

`doctor` checks login tools and noninteractive transfer-host Git/annex commands.
The transfer host must permit Git/ORA remote commands, not just SFTP. In
particular, its noninteractive login PATH must find Git and git-annex; the
profile's `path` is used by the helper and worker. Staging also checks that the
command and transfer hosts see the same store. These checks do not establish
compute-node access, scheduler accounting, MFA reuse or storage durability.

## Prepare and run

Start with a saved, clean DataLad dataset, including its installed subdatasets.
Provide an Apptainer SIF image compatible with the selected cluster. The
request lists exact files, command arguments, validation arguments and required
nonempty output files. Directories and glob patterns are not supported.
Include every script, template, licence file and other dependency the commands
need. Ordinary symlinks are rejected; annexed files are supported.

For example, `request.json` for an image containing Python/nibabel:

```json
{
  "inputs": ["subject/anat.nii.gz", "analyze.py", "validate.py"],
  "command": ["python", "/inputs/analyze.py", "/inputs/subject/anat.nii.gz", "/outputs/summary.json"],
  "validate": ["python", "/inputs/validate.py", "/outputs/summary.json"],
  "outputs": ["summary.json"]
}
```

The validation command must check scientific expectations appropriate to the
analysis, not merely whether a file exists. Commands are argument vectors, not
shell snippets. To run a shell pipeline, include a script in the input dataset.
Output names `execution.json`, `outcome.json`, `worker.log`, `.gitattributes`
and `.gitmodules` are reserved. Validation mounts the output dataset read-only.

```bash
neurodesk-jobs --profile /project/bunya.json --state /project/job-state prepare \
  --dataset /project/dataset --image /project/tool.sif --request /project/request.json
# Use the returned run_id in each following command.
neurodesk-jobs --profile /project/bunya.json --state /project/job-state stage RUN_ID
neurodesk-jobs --profile /project/bunya.json --state /project/job-state submit RUN_ID
neurodesk-jobs --profile /project/bunya.json --state /project/job-state status RUN_ID
neurodesk-jobs --profile /project/bunya.json --state /project/job-state logs RUN_ID
neurodesk-jobs --profile /project/bunya.json --state /project/job-state logs RUN_ID --attempt ATTEMPT_ID
neurodesk-jobs --profile /project/bunya.json --state /project/job-state cancel RUN_ID
```

Preparation creates a new DataLad execution dataset containing selected input
bytes in their original relative layout, a Git bundle for each source dataset,
the SIF, and a checksummed manifest of dataset IDs, commits, commands and files.
This materialized snapshot deliberately avoids runtime subdataset cloning:
workers never follow `.gitmodules` or upstream content URLs. Source bundles
preserve the pinned repository history; only selected content is included.
Each run has its own snapshot, so the pilot trades deduplication and disk space
for simple publication ownership. It does not copy or mutate the source dataset's
working tree, aside from retrieving its requested annex content.

Staging sends this snapshot over SSH to RIA. A successful transfer is labelled
`staged`, with worker verification pending. Before analysis, the worker clones
Git metadata from the filesystem RIA URL, creates a fresh ORA reader with
explicit read and push URLs, retrieves only from that sibling and hashes every
required file. Inherited special-remote configurations are never enabled.
Missing or corrupt input is `inputs-missing` and prevents analysis.
Staging records the tools' versions; workers must report the same versions
before execution. Git transports other than local files are disabled in workers.
Git push URLs are disabled, and the input annex sibling is marked read-only
and ignored after retrieval. Inputs are mounted `/inputs:ro`; outputs are
mounted `/outputs:rw`. These protect against accidental writes by the same
Unix user. Annexed input files are unlocked into ordinary files before mounting
`data/`, so their links cannot break at the container boundary. This is
not a defence against hostile code. `--containall --cleanenv --no-home` isolates the
analysis environment. This does not promise a network sandbox; the site's
offline-worker test must establish that the analysis has no hidden dependency.

The submitted script references an immutable, version-addressed helper zipapp
on shared storage. It launches no persistent process outside the allocation.
Every actual worker start, including a forced restart, creates an exclusive
attempt directory and a **new output dataset ID**, Git repository and annex
storage path. Workers never publish into another attempt or an input repository.
No shared result branches, RIA aliases or result index need a global push lock.

## Recovery and result states

The remote helper atomically claims a run, writes and flushes submission intent,
then calls `sbatch --parsable --no-requeue` once. A permanent claim prevents a
second submission, including after helper failure. The request digest and site
configuration cannot change for an existing run.

If the response is lost, the state remains `submission-unknown`. Repeating
`submit` returns the existing record; it does not call `sbatch` again. `status`
correlates owner, run name and scheduler comment in `squeue`/`sacct`. Accounting
must retain job comments to recover a lost job ID. For a recorded job ID, an
empty accounting comment is accepted only with the matching owner/name/ID.
No matches, unavailable accounting, conflicting matches or a missing journal
remain uncertain. Resolve those cases with the site's scheduler evidence; a
new `prepare` explicitly creates a distinct run and may duplicate work if the
old submission was accepted. There is no “force retry” of an uncertain run.

| Evidence | Meaning |
| --- | --- |
| Accepted job ID | Slurm accepted the request; computation has not necessarily started |
| Attempt `running` | Inputs verified; analysis started |
| Attempt `publishing`, zero analysis/validation exit codes | Computation and validation passed; publication still pending |
| Attempt `site-results-available` and receipt | Output metadata and bytes were copied back from RIA and verified |
| Slurm `COMPLETED`, exit `0:0`, valid receipt | Eligible for collection |
| `collection.json` | Client retrieved and verified one canonical result and its inputs |
| `archive.json` | A self-contained archive was independently restored and verified |

`datalad run` records the actual computation. A separate attempt journal records
failures even when no DataLad commit is produced. Cancellation is a request
until Slurm confirms termination. Timeout, OOM, cancellation, a killed worker
or an unknown scheduler state cannot be overridden by a receipt. Workspaces
are retained after failures. The client chooses the first eligible, verified
attempt as the canonical result; it reports other attempts without merging them.

## Collect, archive and restore

```bash
neurodesk-jobs --profile /project/bunya.json --state /project/job-state collect RUN_ID
neurodesk-jobs --profile /project/bunya.json --state /project/job-state archive RUN_ID
neurodesk-jobs restore /archive/neurodesk-RUN-ATTEMPT.tar.gz /project/restored
```

Collection clones metadata before retrieving bytes and verifies both the
successful result and its execution inputs. The archive contains Git bundles,
all selected input and output content, the SIF, analysis code, manifests and
execution evidence. Archive publication refuses to overwrite an existing file.
Verification restores its datasets into a new directory and checks commits,
identities and hashes. `restore` needs DataLad/Git/annex but no cluster, source
repositories, credentials or scratch store. It rejects archive links and path
traversal. Previously collected data can be archived after accounting expires.

`archive_root` must be a user-confirmed persistent filesystem. For Bunya RDM,
use a permitted mounted archive destination for these bundles, according to
the applicable project policy. This version does not implement a remote-only
RDM uploader. Never put the active RIA store or Git working directories on
QRISdata. A downloaded bundle copied elsewhere outside the tool needs its own
verified receipt and restore check before that destination counts as preserved.

All temporary datasets and scratch siblings are untrusted in annex copy
accounting. The tool never drops data or cleans up old attempts automatically.
Plan byte and inode capacity for snapshot copies, working clones, failed attempts
and verification clones. While the desktop is off, accepted jobs can finish and
publish, but results remain exposed to scratch purging until collection and
archiving occur. A saved receipt is evidence of a past verification, not ongoing
monitoring of the archive's availability or backup policy.

## Site acceptance and development

On **each** site, using synthetic/public data, record:

1. One interactive authentication carrying a full push/get cycle on actual
   command/transfer aliases; disconnect, bounded failure and reauthentication.
2. Atomic claim/journal visibility and concurrent claims from two login nodes.
3. A nested source dataset and staged SIF executing and publishing without
   external worker transfers or credentials; accidental input pushes fail.
4. Interrupted transfer, simultaneous submission retries, lost `sbatch` response
   and delayed/unavailable accounting, with no automatic duplicate submission.
5. Concurrent jobs and repeated worker starts with distinct output dataset IDs.
6. Queued/running cancellation, timeout, OOM, full storage and publication
   failure, with no false success.
7. Desktop shutdown after submission, reconnect/collection, and missing inputs
   failing before analysis. Verify scratch stores cannot justify a last-copy drop.
8. Archive restoration after the scratch store is unavailable. Confirm storage
   permissions, retention, RDM policy and an independent retained input copy.
9. The same analysis at both sites, using predetermined scientific tolerances.

Implementation lives in [`config/remote_jobs`](../../config/remote_jobs/).
The CLI can run from a checkout with `PYTHONPATH=config/remote_jobs python -m
neurodesk_jobs.cli ...`. Unit tests execute scheduler failure scenarios with
small command fixtures. Installed-image tests exercise actual DataLad/RIA and
offline archive restoration; see [testing](../testing.md#remote-compute-jobs).
`datalad-slurm` is not used: the pilot needs the explicit remote submission
journal and independent attempt datasets regardless of scheduling adapter.
