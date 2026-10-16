## In-container Slurm

Neurodesktop starts a local Slurm controller/worker inside the container with a single default queue:

- Partition/queue: `neurodesktop`
- Node: current container hostname
- Limits: detected from host Slurm allocations when available (`SLURM_CPUS_ON_NODE`, `SLURM_JOB_CPUS_PER_NODE`, `SLURM_MEM_PER_NODE`, `SLURM_MEM_PER_CPU`), with cgroup (`cpu.max`, `memory.max`) and system fallbacks

### Slurm modes

Neurodesktop supports two Slurm operation modes:

- `NEURODESKTOP_SLURM_MODE=local` (default outside Apptainer): start and use the in-container single-node Slurm queue.
- `NEURODESKTOP_SLURM_MODE=host`: skip in-container Slurm startup and use the host HPC Slurm cluster.

In `host` mode, Neurodesktop preserves host-provided `SLURM_CONF`, `SBATCH_ACCOUNT`, and `SLURM_ACCOUNT`.
In `local` mode, Neurodesktop sets `SLURM_CONF=/etc/slurm/slurm.conf` and clears inherited account defaults to avoid
`InvalidAccount` against the local slurmdbd setup.
When `NEURODESKTOP_SLURM_MODE` is unset, Neurodesktop automatically defaults to `host` mode
whenever it detects an Apptainer/Singularity runtime.
To force in-container Slurm in Apptainer, set `NEURODESKTOP_SLURM_MODE=local`.

Example for Apptainer on HPC (host Slurm mode, `AuthType=auth/munge`):

```bash
export APPTAINERENV_NEURODESKTOP_SLURM_MODE=host
export APPTAINERENV_SLURM_CONF=/etc/slurm/slurm.conf
export APPTAINERENV_SBATCH_ACCOUNT="${SBATCH_ACCOUNT:-}"
export APPTAINERENV_SLURM_ACCOUNT="${SLURM_ACCOUNT:-}"

apptainer exec \
  --bind /etc/slurm:/etc/slurm \
  --bind /run/munge:/run/munge \
  --bind /var/run/munge:/var/run/munge \
  neurodesktop.sif \
  bash -lc 'sinfo && squeue -u "$USER"'
```

For clusters using `AuthType=auth/slurm` (`sackd`), bind the host Slurm runtime socket path(s) as well:

```bash
apptainer exec \
  --bind /etc/slurm:/etc/slurm \
  --bind /run/slurm:/run/slurm \
  --bind /run/slurmctld:/run/slurmctld \
  --bind /run/slurmdbd:/run/slurmdbd \
  neurodesktop.sif \
  bash -lc 'sinfo && squeue -u "$USER"'
```

If your site uses different paths for `slurm.conf`, MUNGE, or `sack.socket`, bind those paths instead.
Neurodesktop will auto-detect and export `SLURM_CONF`, `MUNGE_SOCKET`, and `SLURM_SACK_SOCKET` in host mode when available.
[`host_slurm_environment.sh`](../jupyter/host_slurm_environment.sh) owns the search: a readable `SLURM_CONF`
that is not the local `ClusterName=neurodesktop` config wins, then `/etc/slurm`, `/etc/slurm-llnl`,
`/run/host/etc/slurm` and `/host/etc/slurm`. An explicit `MUNGE_SOCKET` or `SLURM_SACK_SOCKET` wins when it is a
socket; otherwise the standard `/run` and `/var/run` paths are tried, starting with `/run/slurm-<ClusterName>/sack.socket`.
With `AuthType=auth/slurm` and no socket, an inherited `SLURM_SACK_SOCKET` is cleared. Startup logs a warning
for a missing config or auth socket; interactive shells stay quiet.

### Accounting (MariaDB + slurmdbd)

SLURM 23.11+ (as shipped in the Ubuntu 24.04 packages) rejects jobs with
`Reason=InvalidAccount` when `AccountingStorageType=accounting_storage/none`
is used and no user-account associations exist.

To fix this, the startup script brings up a minimal **MariaDB + slurmdbd** stack:

1. **MariaDB** starts socket-only (`--skip-networking`) — no TCP port is exposed.
2. A `slurm_acct_db` database is created with a passwordless local `slurm` user.
3. **slurmdbd** starts and connects to MariaDB via the Unix socket.
4. After `slurmctld` and `slurmd` are running, `sacctmgr` creates:
   - Cluster: `neurodesktop`
   - Account: `default`
   - Users: the notebook user (`$NB_USER`, typically `jovyan`) and `root`

If MariaDB or slurmdbd fail to start, the script falls back to
`AccountingStorageType=accounting_storage/none` with a warning. The container
still starts, but jobs may pend with `InvalidAccount` on SLURM 23.11+.

This means `sbatch`/`srun` jobs submitted inside the container stay inside the container and cannot exceed the configured node CPU/memory limits.

### Running an ASTRA analysis with `lc`

Run `neurodesk-lc-materialize baseline/OUTPUT` from a committed Lightcone 0.5
project with an explicit `[tool.neurodesk.lightcone]` module profile. The helper
owns native Slurm allocation, readiness, materialization, and cleanup. Do not
submit it through `sbatch`. It consumes the project's exact module pins and
leaves Lightcone's maintained `ro-crate-metadata.json` as execution evidence.

The installed catalog requests two CPUs and 8 GiB in partition `neurodesktop`.
Set `LC_COMPUTE_CONFIG` or provide `~/.lightcone/compute.yaml` for other resource
shapes or host clusters. Local fallback is disabled in the installed catalog.
See [ASTRA integration](../../docs/architecture/astra.md) for project setup,
worker module execution, open-filesystem attestation, and viewer trust.

The default plain `sbatch` workflow remains available. It writes provenance
sidecars and does not establish Lightcone execution evidence.

### Environment variables

- `NEURODESKTOP_SLURM_MODE=local|host` to select in-container (`local`) or host-cluster (`host`) Slurm mode
- `NEURODESKTOP_SLURM_ENABLE=0` to disable local Slurm startup
- `NEURODESKTOP_SLURM_MEMORY_RESERVE_MB=256` memory headroom reserved for desktop/Jupyter processes
- `NEURODESKTOP_SLURM_PARTITION=neurodesktop` to rename the partition
- `NEURODESKTOP_MUNGE_NUM_THREADS=10` to control munged worker threads for Slurm auth traffic
- `NEURODESKTOP_SLURM_USE_CGROUP=0` to force non-cgroup mode
- `NEURODESKTOP_SLURM_USE_CGROUP=1` to opt in to cgroup mode when compatible cgroups are available
- `NEURODESKTOP_SLURM_CGROUP_PLUGIN=autodetect` to override the cgroup plugin (`cgroup/v1`, `cgroup/v2`, etc.)
- `NEURODESKTOP_SLURM_CGROUP_MOUNTPOINT=/sys/fs/cgroup` to override the cgroup mountpoint path
- `NEURODESKTOP_SLURM_LEGACY_CGROUP_PLUGIN=cgroup/v1` to override legacy compatibility fallback plugin
- `NEURODESKTOP_SLURM_LEGACY_CGROUP_MOUNTPOINT=/tmp/cgroup` to override legacy compatibility fallback mountpoint
- `NEURODESKTOP_SLURM_ENABLE_TASK_AFFINITY=1` to opt in to `task/affinity` (default is disabled for container compatibility)

### Limit detection order (local mode)

In local mode, `setup_and_start_slurm.sh` sizes the single-node queue from the most restrictive values it can detect:

1. Host Slurm job environment (preferred in Apptainer on HPC): `SLURM_CPUS_ON_NODE`, `SLURM_JOB_CPUS_PER_NODE`, `SLURM_CPUS_PER_TASK`, `SLURM_MEM_PER_NODE`, `SLURM_MEM_PER_CPU`
2. Container cgroup limits (v2 and v1 paths)
3. Host-visible defaults (`nproc`, `/proc/meminfo`)

### Cgroup mode

`setup_and_start_slurm.sh` defaults to non-cgroup mode in `NEURODESKTOP_SLURM_USE_CGROUP=auto`
for container compatibility. This sets:
- `ProctrackType=proctrack/linuxproc`
- `TaskPlugin=task/none`
- `JobAcctGatherType=jobacct_gather/none`
- compatibility `cgroup.conf` (`CgroupPlugin=cgroup/v1`, `CgroupMountpoint=/tmp/cgroup`)

When cgroup mode is enabled (`NEURODESKTOP_SLURM_USE_CGROUP=1`) and compatible cgroups are available,
the script writes `cgroup.conf` with `CgroupPlugin=autodetect`, mountpoint `/sys/fs/cgroup`,
and `IgnoreSystemd=yes`, then prepares `/sys/fs/cgroup/system.slice/<hostname>_slurmstepd.scope`
before starting `slurmd`.
If `slurmd` still fails with cgroup scope/mount errors, startup automatically falls back to
non-cgroup mode and retries once.
Startup logs include:
- `SLURM fallback activated: ...`
- `slurmd started successfully using non-cgroup fallback (...)`
In non-cgroup mode, cgroup constraints are disabled (no CPU/RAM/SWAP enforcement by Slurm).

### Batch job environments

Slurm 25.11.2 ignores the obsolete `GetEnvTimeout` setting. Its
[login-environment implementation](https://github.com/SchedMD/slurm/blob/slurm-25-11-2-1/src/common/env.c)
uses PID and mount namespaces, with an existing `fork()` fallback when the
kernel disables those namespaces. Ordinary Docker containers deny the namespace
`clone()` with `EPERM` even when the kernel namespace limits are nonzero.
Unpatched Slurm treats that denial as fatal and exits the worker.

The image applies [a narrow source patch](user-env-25.11.2.patch) that routes
this permission denial through Slurm's existing fallback. Successful namespace
creation and other clone errors keep their upstream behavior. The build verifies
the release archive and complete `env.c` SHA-256 before applying the patch.
It compiles that source into a library exporting only `env_array_user_default`.
Only the private `/opt/neurodesktop/slurm-env/slurmd` executable links this library.
The packaged worker, clients, plugins, and job processes remain unchanged.
No `LD_PRELOAD`, syscall override, environment cache, or extra capability is used.
Worker reconfiguration re-executes the private executable. Startup refuses to
use it if the packaged daemon or Slurm library has changed since the build.

`--get-user-env`, `--export=NONE`, and selected-variable exports retrieve the
job owner's actual login environment through upstream `su - <user>`, parsing,
and merge logic. Explicitly exported values take precedence over login values.
`ALL` inherits the submission environment. `NIL` omits that environment and
does not request a login environment. Failed retrieval retains Slurm 25.11's
requeue-and-hold behavior. The upstream 120-second retrieval timeout remains.
Host mode skips this local setup and uses the host cluster's policy.

The fallback logs upstream's warning about orphan or unconstrained processes.
It cannot contain background processes started by login scripts in a separate
PID namespace. Keep login scripts limited to environment setup. Slurm's normal
per-user credentials still apply, and one user's login environment is never
reused for another user.

The local controller sets `AccountingStoreFlags=job_comment`, supported by the
[25.11 configuration manual](https://github.com/SchedMD/slurm/blob/slurm-25-11-2-1/doc/man/man5/slurm.conf.5).
Accounting retains a completed or cancelled allocation's `lightcone:<token>`
comment so Lightcone can verify its identity through `sacct`. No environment or
job-script accounting flags are enabled.

Use `--export=ALL` to inherit the submitting environment. For a clean environment,
use `--export=NIL` and initialize what the job needs inside its script, for example:

```bash
#!/bin/bash
set -euo pipefail
source /opt/neurodesktop/agent_bash_env.sh
module load fsl/6.0.7.22
```

After a worker crash, save `/var/log/slurm/slurmd.log`, cancel the affected job
IDs, and restart the server with the corrected image. Changing export options
inside a batch script cannot repair a worker that exits before the script starts.

### Testing

Quick smoke test inside the container:

```bash
/opt/neurodesktop/test_slurm_setup.sh --bootstrap
```

From a checkout with Docker access, validate the installed scheduler under
default Docker permissions, including namespace-restricted environment retrieval:

```bash
bash scripts/verify_slurm_image.sh IMAGE
```

Submit test job (end-to-end `sbatch` + `srun`):

```bash
jobid=$(sbatch --parsable /opt/neurodesktop/slurm_submit_smoke.sbatch)
echo "Submitted job ${jobid}"
squeue -j "${jobid}"
tail -f "/tmp/nd-slurm-smoke-${jobid}.out"
```

If you changed the partition name, override on submit:

```bash
sbatch -p "${NEURODESKTOP_SLURM_PARTITION}" /opt/neurodesktop/slurm_submit_smoke.sbatch
```

In `local` mode, Neurodesktop clears inherited `SBATCH_ACCOUNT` and `SLURM_ACCOUNT`
in `/opt/neurodesktop/environment_variables.sh` to avoid local-account mismatches.
