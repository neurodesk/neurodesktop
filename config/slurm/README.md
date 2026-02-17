## In-container Slurm

Neurodesktop starts a local Slurm controller/worker inside the container with a single default queue:

- Partition/queue: `neurodesktop`
- Node: current container hostname
- Limits: detected from container cgroups (`cpu.max`, `memory.max`), with optional Slurm cgroup enforcement when cgroup mode is enabled

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

### Cgroup mode

`setup_and_start_slurm.sh` defaults to non-cgroup mode in `NEURODESKTOP_SLURM_USE_CGROUP=auto`
for container compatibility. This sets:
- `ProctrackType=proctrack/linuxproc`
- `TaskPlugin=task/affinity`
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

To prevent inherited host-cluster defaults from causing account errors, Neurodesktop clears `SBATCH_ACCOUNT`
and `SLURM_ACCOUNT` in `/opt/neurodesktop/environment_variables.sh`.
