import os
import subprocess
import time
import pytest


def run_cmd(cmd):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return process.returncode, process.stdout.strip()


def _can_run_root_cmds():
    if os.geteuid() == 0:
        return True
    code, _ = run_cmd("sudo -n true")
    return code == 0


class TestDeferredStartup:
    """Tests for the lazy/eager startup mode infrastructure."""

    def test_deferred_startup_script_exists(self):
        """Verify the deferred startup script is installed and executable."""
        script = "/opt/neurodesktop/deferred_startup.sh"
        assert os.path.isfile(script), f"{script} not found"
        assert os.access(script, os.X_OK), f"{script} is not executable"

    def test_deferred_startup_log(self):
        """In lazy mode (default), the deferred startup log should exist."""
        cvmfs_mode = os.environ.get("NEURODESKTOP_CVMFS_STARTUP_MODE", "lazy")
        slurm_mode = os.environ.get("NEURODESKTOP_SLURM_STARTUP_MODE", "lazy")

        if cvmfs_mode != "lazy" and slurm_mode != "lazy":
            pytest.skip("Neither CVMFS nor Slurm is in lazy mode")

        log_file = "/tmp/neurodesktop-deferred-startup.log"
        # Wait up to 5s for the log file to appear (worker starts in background)
        deadline = time.time() + 5
        while time.time() < deadline:
            if os.path.isfile(log_file):
                break
            time.sleep(0.5)

        assert os.path.isfile(log_file), (
            f"Deferred startup log not found at {log_file}. "
            "The deferred worker may not have been launched."
        )

    def test_deferred_startup_completes(self):
        """The deferred startup worker should eventually complete."""
        cvmfs_mode = os.environ.get("NEURODESKTOP_CVMFS_STARTUP_MODE", "lazy")
        slurm_mode = os.environ.get("NEURODESKTOP_SLURM_STARTUP_MODE", "lazy")

        if cvmfs_mode != "lazy" and slurm_mode != "lazy":
            pytest.skip("Neither CVMFS nor Slurm is in lazy mode")

        done_file = "/tmp/neurodesktop-deferred-startup.done"
        # Allow generous time for CVMFS probing + Slurm startup
        deadline = time.time() + 180
        while time.time() < deadline:
            if os.path.isfile(done_file):
                return
            time.sleep(2)

        # Read the log for diagnostics
        log_file = "/tmp/neurodesktop-deferred-startup.log"
        log_content = ""
        if os.path.isfile(log_file):
            with open(log_file) as f:
                log_content = f.read()

        pytest.fail(
            f"Deferred startup did not complete within 180s.\n"
            f"Log:\n{log_content}"
        )

    def test_timing_logs_present(self):
        """Phase timing logs should be present in deferred startup log."""
        cvmfs_mode = os.environ.get("NEURODESKTOP_CVMFS_STARTUP_MODE", "lazy")
        slurm_mode = os.environ.get("NEURODESKTOP_SLURM_STARTUP_MODE", "lazy")

        if cvmfs_mode != "lazy" and slurm_mode != "lazy":
            pytest.skip("Neither CVMFS nor Slurm is in lazy mode")

        log_file = "/tmp/neurodesktop-deferred-startup.log"
        done_file = "/tmp/neurodesktop-deferred-startup.done"

        # Wait for completion first
        deadline = time.time() + 180
        while time.time() < deadline:
            if os.path.isfile(done_file):
                break
            time.sleep(2)

        if not os.path.isfile(log_file):
            pytest.skip("Deferred startup log not found")

        with open(log_file) as f:
            log_content = f.read()

        assert "[TIMING]" in log_content, (
            "No [TIMING] entries found in deferred startup log"
        )


class TestSlurmEventualReadiness:
    """When Slurm is enabled and lazy, it should become ready after Jupyter."""

    def test_slurm_responds_after_deferred(self):
        """After deferred startup completes, scontrol ping should succeed."""
        slurm_mode = os.environ.get("NEURODESKTOP_SLURM_STARTUP_MODE", "lazy")
        slurm_enable = os.environ.get("NEURODESKTOP_SLURM_ENABLE", "1")
        neurodesktop_slurm_mode = os.environ.get("NEURODESKTOP_SLURM_MODE", "local")

        if slurm_mode != "lazy":
            pytest.skip("Slurm is not in lazy mode")
        if slurm_enable == "0":
            pytest.skip("Slurm is disabled")
        if neurodesktop_slurm_mode == "host":
            pytest.skip("Slurm is in host mode")

        # Wait for deferred startup to complete
        done_file = "/tmp/neurodesktop-deferred-startup.done"
        deadline = time.time() + 180
        while time.time() < deadline:
            if os.path.isfile(done_file):
                break
            time.sleep(2)

        if not os.path.isfile(done_file):
            pytest.skip("Deferred startup did not complete")

        # Now check that Slurm is responding
        # Give it a few extra seconds after deferred_startup.done
        deadline = time.time() + 30
        while time.time() < deadline:
            code, output = run_cmd("scontrol ping")
            if code == 0 and "UP" in output.upper():
                return
            time.sleep(2)

        pytest.fail(
            f"scontrol ping did not succeed within 30s after deferred startup completed. "
            f"Last output: {output}"
        )


class TestEagerStartupMode:
    """Verify eager mode preserves synchronous startup behavior."""

    def test_eager_no_deferred_worker(self):
        """When CVMFS and Slurm are both eager, no deferred worker log should exist."""
        cvmfs_mode = os.environ.get("NEURODESKTOP_CVMFS_STARTUP_MODE", "lazy")
        slurm_mode = os.environ.get("NEURODESKTOP_SLURM_STARTUP_MODE", "lazy")

        if cvmfs_mode != "eager" or slurm_mode != "eager":
            pytest.skip("Not running in full eager mode")

        log_file = "/tmp/neurodesktop-deferred-startup.log"
        assert not os.path.isfile(log_file), (
            "Deferred startup log should not exist when both CVMFS and Slurm are eager"
        )


class TestCvmfsSelectionCache:
    """Verify CVMFS selection is cached for subsequent boots."""

    def test_cvmfs_cache_exists(self):
        """After CVMFS mounts successfully, a cache file should exist."""
        cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
        if cvmfs_disable in ["true", "1"]:
            pytest.skip("CVMFS is disabled")

        if not os.path.isdir("/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules/"):
            pytest.skip("CVMFS is not mounted")

        cvmfs_mode = os.environ.get("NEURODESKTOP_CVMFS_STARTUP_MODE", "lazy")
        if cvmfs_mode != "lazy":
            pytest.skip("CVMFS is not in lazy mode (cache only used in lazy mode)")

        cache_file = os.path.expanduser("~/.cache/neurodesktop/cvmfs-selection.env")
        assert os.path.isfile(cache_file), (
            f"CVMFS selection cache not found at {cache_file}"
        )

        with open(cache_file) as f:
            content = f.read()

        assert "CACHED_REGION=" in content, "CACHED_REGION not found in cache file"
        assert "CACHED_MODE=" in content, "CACHED_MODE not found in cache file"
