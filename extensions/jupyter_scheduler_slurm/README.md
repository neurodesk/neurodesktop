# jupyter-scheduler-slurm

Slurm-backed execution manager for `jupyter_scheduler`.

This package is intentionally small and is configured in Neurodesktop via:

- `c.Scheduler.execution_manager_class = "jupyter_scheduler_slurm.executors.SlurmExecutionManager"`

Neurodesktop enables this backend when:

- `NEURODESKTOP_JUPYTER_SCHEDULER_BACKEND=slurm`
