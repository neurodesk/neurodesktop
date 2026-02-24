import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional

import nbformat

from jupyter_scheduler.executors import DefaultExecutionManager
from jupyter_scheduler.models import JobFeature, Status
from jupyter_scheduler.orm import Job
from jupyter_scheduler.parameterize import add_parameters


class SlurmExecutionManager(DefaultExecutionManager):
    """Execute notebook jobs by submitting them to Slurm with sbatch."""

    _PENDING_STATES = {
        "PENDING",
        "CONFIGURING",
        "RESIZING",
        "SUSPENDED",
    }
    _RUNNING_STATES = {
        "RUNNING",
        "COMPLETING",
        "STAGE_OUT",
        "SIGNALING",
    }
    _SUCCESS_STATE = "COMPLETED"
    _FAILURE_STATES = {
        "BOOT_FAIL",
        "CANCELLED",
        "DEADLINE",
        "FAILED",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "REVOKED",
        "TIMEOUT",
        "UNKNOWN",
    }
    _TERMINAL_STATES = _FAILURE_STATES | {_SUCCESS_STATE}

    def execute(self):
        job = self.model
        staging_dir = Path(self.staging_paths["input"]).parent
        staging_dir.mkdir(parents=True, exist_ok=True)

        with open(self.staging_paths["input"], encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)

        if job.parameters:
            nb = add_parameters(nb, job.parameters)

        try:
            kernel_name = nb.metadata.kernelspec["name"]
        except Exception as exc:
            raise RuntimeError(
                "Notebook is missing kernelspec metadata required for Slurm execution."
            ) from exc

        param_notebook_path = staging_dir / f".scheduler-param-{self.job_id}.ipynb"
        with open(param_notebook_path, "w", encoding="utf-8") as f:
            nbformat.write(nb, f)

        # Always execute to an ipynb path so we can export configured output formats afterwards.
        executed_notebook_path = Path(
            self.staging_paths.get("ipynb", str(staging_dir / f"{self.job_id}-executed.ipynb"))
        )
        stdout_path = staging_dir / f"slurm-{self.job_id}.out"
        stderr_path = staging_dir / f"slurm-{self.job_id}.err"
        sbatch_script_path = staging_dir / f".scheduler-slurm-{self.job_id}.sbatch"

        self._write_submission_script(
            script_path=sbatch_script_path,
            work_dir=staging_dir,
            input_notebook_path=param_notebook_path,
            output_notebook_path=executed_notebook_path,
            kernel_name=kernel_name,
        )

        slurm_job_id = self._submit_sbatch_job(
            script_path=sbatch_script_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            compute_type=job.compute_type,
            job_name=job.name,
        )

        self._update_job_record(
            pid=slurm_job_id,
            status=Status.QUEUED.value,
            status_message=f"Submitted to Slurm as job {slurm_job_id}",
        )

        final_state = self._wait_for_terminal_state(
            slurm_job_id=slurm_job_id,
            output_notebook_path=executed_notebook_path,
        )

        if final_state != self._SUCCESS_STATE:
            raise RuntimeError(
                "Slurm job "
                f"{slurm_job_id} finished with state {final_state}. "
                f"See logs: {stdout_path} and {stderr_path}."
            )

        if not executed_notebook_path.exists():
            raise RuntimeError(
                "Slurm job "
                f"{slurm_job_id} reported COMPLETED but output notebook was not created: "
                f"{executed_notebook_path}"
            )

        with open(executed_notebook_path, encoding="utf-8") as f:
            executed_nb = nbformat.read(f, as_version=4)

        self.create_output_files(job, executed_nb)

        # Keep side-effects parity with the default scheduler behavior.
        self.add_side_effects_files(str(staging_dir))

        self._cleanup_internal_files(sbatch_script_path, param_notebook_path)
        self._update_job_record(status_message=f"Completed via Slurm job {slurm_job_id}")

    def _cleanup_internal_files(self, *paths: Path):
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                # Cleanup should not fail the job.
                pass

    def _write_submission_script(
        self,
        script_path: Path,
        work_dir: Path,
        input_notebook_path: Path,
        output_notebook_path: Path,
        kernel_name: str,
    ):
        papermill_cmd = " ".join(
            [
                "papermill",
                "--kernel",
                shlex.quote(kernel_name),
                shlex.quote(str(input_notebook_path)),
                shlex.quote(str(output_notebook_path)),
            ]
        )

        script = "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"cd {shlex.quote(str(work_dir))}",
                papermill_cmd,
                "",
            ]
        )

        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o755)

    def _submit_sbatch_job(
        self,
        script_path: Path,
        stdout_path: Path,
        stderr_path: Path,
        compute_type: Optional[str],
        job_name: str,
    ) -> int:
        cmd = [
            "sbatch",
            "--parsable",
            "--output",
            str(stdout_path),
            "--error",
            str(stderr_path),
        ]

        slurm_job_name = self._build_job_name(job_name)
        if slurm_job_name:
            cmd.extend(["--job-name", slurm_job_name])

        partition = (compute_type or os.getenv("NEURODESKTOP_JUPYTER_SCHEDULER_SLURM_PARTITION", "")).strip()
        if partition:
            cmd.extend(["--partition", partition])

        account = self._resolve_slurm_account()
        if account:
            cmd.extend(["--account", account])

        time_limit = os.getenv("NEURODESKTOP_JUPYTER_SCHEDULER_SLURM_TIME", "").strip()
        if time_limit:
            cmd.extend(["--time", time_limit])

        memory_limit = os.getenv("NEURODESKTOP_JUPYTER_SCHEDULER_SLURM_MEM", "").strip()
        if memory_limit:
            cmd.extend(["--mem", memory_limit])

        cpus_per_task = os.getenv("NEURODESKTOP_JUPYTER_SCHEDULER_SLURM_CPUS_PER_TASK", "").strip()
        if cpus_per_task:
            cmd.extend(["--cpus-per-task", cpus_per_task])

        qos = os.getenv("NEURODESKTOP_JUPYTER_SCHEDULER_SLURM_QOS", "").strip()
        if qos:
            cmd.extend(["--qos", qos])

        extra_args = os.getenv("NEURODESKTOP_JUPYTER_SCHEDULER_SLURM_EXTRA_ARGS", "").strip()
        if extra_args:
            cmd.extend(shlex.split(extra_args))

        cmd.append(str(script_path))

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "").strip() or "unknown sbatch error"
            raise RuntimeError(f"sbatch submission failed: {message}")

        output = (proc.stdout or "").strip()
        token = output.split(";", 1)[0].strip()
        if not token.isdigit():
            raise RuntimeError(f"Could not parse sbatch job id from output: {output}")

        return int(token)

    def _wait_for_terminal_state(self, slurm_job_id: int, output_notebook_path: Path) -> str:
        poll_seconds = self._safe_int_from_env(
            "NEURODESKTOP_JUPYTER_SCHEDULER_SLURM_POLL_INTERVAL_SECONDS", 5
        )
        poll_seconds = max(1, poll_seconds)
        max_missing_polls = self._safe_int_from_env(
            "NEURODESKTOP_JUPYTER_SCHEDULER_SLURM_MAX_MISSING_POLLS", 24
        )

        missing_polls = 0
        running_reported = False

        while True:
            state = (
                self._query_squeue_state(slurm_job_id)
                or self._query_sacct_state(slurm_job_id)
                or self._query_scontrol_state(slurm_job_id)
            )

            if not state:
                missing_polls += 1
                if missing_polls >= max_missing_polls:
                    if output_notebook_path.exists():
                        return self._SUCCESS_STATE
                    return "UNKNOWN"
                time.sleep(poll_seconds)
                continue

            missing_polls = 0

            if state in self._PENDING_STATES:
                self._update_job_record(
                    status=Status.QUEUED.value,
                    status_message=f"Waiting in Slurm queue (job {slurm_job_id}, state {state})",
                )
            elif state in self._RUNNING_STATES and not running_reported:
                self._update_job_record(
                    status=Status.IN_PROGRESS.value,
                    status_message=f"Running in Slurm (job {slurm_job_id})",
                )
                running_reported = True

            if state in self._TERMINAL_STATES:
                return state

            time.sleep(poll_seconds)

    def _query_squeue_state(self, slurm_job_id: int) -> Optional[str]:
        proc = subprocess.run(
            ["squeue", "-h", "-j", str(slurm_job_id), "-o", "%T"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None

        for raw_line in (proc.stdout or "").splitlines():
            state = self._normalize_state(raw_line)
            if state:
                return state

        return None

    def _query_sacct_state(self, slurm_job_id: int) -> Optional[str]:
        proc = subprocess.run(
            ["sacct", "-n", "-P", "-j", str(slurm_job_id), "-o", "JobIDRaw,State"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None

        expected_id = str(slurm_job_id)
        fallback_state = None

        for raw_line in (proc.stdout or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue

            job_id_raw = parts[0].strip()
            state = self._normalize_state(parts[1])
            if not state:
                continue

            if job_id_raw == expected_id:
                return state

            if not fallback_state and job_id_raw.startswith(expected_id):
                fallback_state = state

        return fallback_state

    def _query_scontrol_state(self, slurm_job_id: int) -> Optional[str]:
        proc = subprocess.run(
            ["scontrol", "show", "job", str(slurm_job_id), "-o"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None

        for token in (proc.stdout or "").split():
            if token.startswith("JobState="):
                return self._normalize_state(token.split("=", 1)[1])

        return None

    def _update_job_record(self, **fields):
        with self.db_session() as session:
            session.query(Job).filter(Job.job_id == self.job_id).update(fields)
            session.commit()

    def _resolve_slurm_account(self) -> str:
        for env_name in (
            "NEURODESKTOP_JUPYTER_SCHEDULER_SLURM_ACCOUNT",
            "SBATCH_ACCOUNT",
            "SLURM_ACCOUNT",
        ):
            value = os.getenv(env_name, "").strip()
            if value:
                return value
        return ""

    def _build_job_name(self, job_name: str) -> str:
        candidate = f"{job_name}-{self.job_id[:8]}" if job_name else f"nd-{self.job_id[:8]}"
        # Keep Slurm job names simple and bounded.
        candidate = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in candidate)
        return candidate[:120]

    def _normalize_state(self, raw_state: str) -> str:
        state = (raw_state or "").strip().upper()
        if not state:
            return ""
        state = state.split()[0]
        state = state.split("+", 1)[0]
        return state

    def _safe_int_from_env(self, env_name: str, default: int) -> int:
        value = os.getenv(env_name, "").strip()
        if not value:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def supported_features(cls) -> Dict[JobFeature, bool]:
        return {
            JobFeature.job_name: True,
            JobFeature.output_formats: True,
            JobFeature.job_definition: False,
            JobFeature.idempotency_token: False,
            JobFeature.tags: False,
            JobFeature.email_notifications: False,
            JobFeature.timeout_seconds: False,
            JobFeature.retry_on_timeout: False,
            JobFeature.max_retries: False,
            JobFeature.min_retry_interval_millis: False,
            JobFeature.output_filename_template: False,
            JobFeature.stop_job: False,
            JobFeature.delete_job: False,
        }
