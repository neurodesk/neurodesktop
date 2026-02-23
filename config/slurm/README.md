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

Example for Apptainer on HPC (host Slurm mode):

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

If your site uses a different path for `slurm.conf` or the MUNGE socket, bind those paths instead.
Neurodesktop will auto-detect and export `SLURM_CONF` and `MUNGE_SOCKET` in host mode when those paths are present.

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

### Testing

Quick smoke test inside the container:

```bash
/opt/neurodesktop/test_slurm_setup.sh --bootstrap
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
