## In-container Slurm

Neurodesktop starts a local Slurm controller/worker inside the container with a single default queue:

- Partition/queue: `neurodesktop`
- Node: current container hostname
- Limits: detected from container cgroups (`cpu.max`, `memory.max`) and enforced with Slurm cgroup plugins

This means `sbatch`/`srun` jobs submitted inside the container stay inside the container and cannot exceed the container CPU/memory cgroup limits.

Optional environment variables:

- `NEURODESKTOP_SLURM_ENABLE=0` to disable local Slurm startup
- `NEURODESKTOP_SLURM_MEMORY_RESERVE_MB=256` memory headroom reserved for desktop/Jupyter processes
- `NEURODESKTOP_SLURM_PARTITION=neurodesktop` to rename the partition
- `NEURODESKTOP_MUNGE_NUM_THREADS=10` to control munged worker threads for Slurm auth traffic
- `NEURODESKTOP_SLURM_USE_CGROUP=0` to force non-cgroup mode
- `NEURODESKTOP_SLURM_CGROUP_PLUGIN=autodetect` to override the cgroup plugin (`cgroup/v1`, `cgroup/v2`, etc.)
- `NEURODESKTOP_SLURM_CGROUP_MOUNTPOINT=/sys/fs/cgroup` to override the cgroup mountpoint path

`setup_and_start_slurm.sh` writes `cgroup.conf` with `CgroupPlugin=autodetect`
and mountpoint `/sys/fs/cgroup` by default.
In `NEURODESKTOP_SLURM_USE_CGROUP=auto` mode, Slurm cgroup mode is disabled automatically
when the container cgroup layout is incompatible (for example read-only cgroup v2 `system.slice`).
In non-cgroup mode, cgroup constraints are disabled (no CPU/RAM/SWAP enforcement by Slurm).

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
