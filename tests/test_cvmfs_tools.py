import subprocess
import os
import signal
import pytest


# Preamble that mirrors the real user environment: sources environment_variables.sh
# (sets MODULEPATH via glob, neurodesk_singularity_opts, etc.) and initialises lmod.
_ENV_PREAMBLE = (
    "source /opt/neurodesktop/environment_variables.sh 2>/dev/null; "
    "source /usr/share/lmod/lmod/init/bash 2>/dev/null; "
)


def run_cmd(cmd, timeout=120):
    """Run a shell command and return (exit_code, output). Kills process group on timeout."""
    process = subprocess.Popen(
        cmd,
        shell=True,
        executable="/bin/bash",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
        return process.returncode, stdout.strip()
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise


def run_neuro_cmd(cmd, timeout=120):
    """Run a command inside the full neurodesk environment (lmod + singularity opts)."""
    return run_cmd(_ENV_PREAMBLE + cmd, timeout=timeout)


def _cvmfs_should_be_mounted():
    """Return True if CVMFS is expected to be available in this environment."""
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    return cvmfs_disable not in ["true", "1"]


def _skip_if_cvmfs_disabled():
    """Skip the test if CVMFS is intentionally disabled."""
    if not _cvmfs_should_be_mounted():
        pytest.skip("CVMFS is disabled (CVMFS_DISABLE=true)")


def _fsl_available():
    """Check if FSL module loads and fslmaths is on PATH."""
    try:
        code, _ = run_neuro_cmd(
            "module load fsl 2>/dev/null; command -v fslmaths",
            timeout=120,
        )
        return code == 0
    except subprocess.TimeoutExpired:
        return False


def test_cvmfs_setup_when_enabled():
    """When CVMFS is expected, verify it was actually mounted and has modules."""
    if not _cvmfs_should_be_mounted():
        pytest.skip("CVMFS is disabled — nothing to assert")

    assert os.path.isdir("/cvmfs/neurodesk.ardc.edu.au"), (
        "CVMFS is enabled but /cvmfs/neurodesk.ardc.edu.au is not mounted — "
        "startup scripts failed to mount CVMFS"
    )
    modules_dir = "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules"
    assert os.path.isdir(modules_dir), (
        "CVMFS is mounted but neurodesk-modules directory is missing at "
        f"{modules_dir}"
    )


class TestCvmfsMount:
    """Verify CVMFS is mounted and neurodesk modules are accessible."""

    def test_cvmfs_neurodesk_mounted(self):
        """CVMFS neurodesk repository should be mounted and contain modules."""
        _skip_if_cvmfs_disabled()

        modules_dir = "/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules"
        assert os.path.isdir(modules_dir), (
            f"Neurodesk modules directory not found at {modules_dir}"
        )

    def test_cvmfs_contains_containers(self):
        """CVMFS should contain the containers directory."""
        _skip_if_cvmfs_disabled()

        containers_dir = "/cvmfs/neurodesk.ardc.edu.au/containers"
        assert os.path.isdir(containers_dir), (
            f"Containers directory not found at {containers_dir}"
        )

        # Should have at least one container
        entries = os.listdir(containers_dir)
        assert len(entries) > 0, "No containers found in CVMFS containers directory"

    def test_cvmfs_fsl_container_exists(self):
        """An FSL container should be available on CVMFS."""
        _skip_if_cvmfs_disabled()

        containers_dir = "/cvmfs/neurodesk.ardc.edu.au/containers"
        if not os.path.isdir(containers_dir):
            pytest.skip("CVMFS containers directory not available")

        fsl_containers = [
            d for d in os.listdir(containers_dir) if d.startswith("fsl_")
        ]
        assert len(fsl_containers) > 0, (
            "No FSL container found in CVMFS. "
            f"Available containers: {os.listdir(containers_dir)[:20]}"
        )


class TestFslMaths:
    """Verify FSL can be loaded and fslmaths runs correctly via CVMFS."""

    def test_fsl_module_loads(self):
        """FSL module should load and put fslmaths on PATH."""
        _skip_if_cvmfs_disabled()

        code, output = run_neuro_cmd(
            "module load fsl 2>/dev/null; command -v fslmaths"
        )
        assert code == 0, (
            f"fslmaths not found in PATH after loading FSL module: {output}"
        )

    def test_fslmaths_runs(self, tmp_path):
        """fslmaths should execute and produce correct output on a test image."""
        _skip_if_cvmfs_disabled()
        if not _fsl_available():
            pytest.skip("FSL module could not be loaded")

        output_image = str(tmp_path / "test_output.nii.gz")

        # Run fslmaths inside the full neurodesk environment
        # Timeout is generous: on arm64, fslmaths runs via QEMU emulation
        code, output = run_neuro_cmd(
            f"module load fsl 2>/dev/null; "
            f'FSLDIR="${{FSLDIR:-}}"; '
            f'test_img="$FSLDIR/data/standard/MNI152_T1_2mm.nii.gz"; '
            f'[ -f "$test_img" ] || test_img="$FSLDIR/data/standard/MNI152_T1_1mm.nii.gz"; '
            f'[ -f "$test_img" ] || {{ echo "No FSL test image found. FSLDIR=$FSLDIR"; exit 2; }}; '
            f'fslmaths "$test_img" -add 0 {output_image}',
            timeout=600,
        )
        if code == 2:
            pytest.skip(f"No standard FSL test image found: {output}")
        assert code == 0, f"fslmaths failed with exit code {code}: {output}"
        assert os.path.isfile(output_image), (
            f"fslmaths did not produce output file at {output_image}"
        )

        output_size = os.path.getsize(output_image)
        assert output_size > 0, "Output image is empty"

    def test_nonexistent_module_fails(self):
        """Loading a non-existent module should fail with non-zero exit code."""
        _skip_if_cvmfs_disabled()

        code, output = run_neuro_cmd("module load funny-name-tool")
        assert code != 0, (
            f"Loading non-existent module should have failed but exited 0: {output}"
        )

    def test_failed_load_doesnt_break_env(self):
        """Loading a bogus module should not prevent loading a valid one afterwards."""
        _skip_if_cvmfs_disabled()
        if not _fsl_available():
            pytest.skip("FSL module could not be loaded")

        code, output = run_neuro_cmd(
            "module load funny-name-tool 2>/dev/null || true; "
            "module load fsl 2>/dev/null; "
            "command -v fslmaths"
        )
        assert code == 0, (
            f"fslmaths should be available after loading fsl, even after a prior "
            f"bogus module load: {output}"
        )

    def test_fslmaths_arithmetic(self, tmp_path):
        """fslmaths should correctly perform arithmetic operations."""
        _skip_if_cvmfs_disabled()
        if not _fsl_available():
            pytest.skip("FSL module could not be loaded")

        multiplied = str(tmp_path / "multiplied.nii.gz")

        code, output = run_neuro_cmd(
            f"module load fsl 2>/dev/null; "
            f'FSLDIR="${{FSLDIR:-}}"; '
            f'test_img="$FSLDIR/data/standard/MNI152_T1_2mm.nii.gz"; '
            f'[ -f "$test_img" ] || test_img="$FSLDIR/data/standard/MNI152_T1_1mm.nii.gz"; '
            f'[ -f "$test_img" ] || {{ echo "No FSL test image found. FSLDIR=$FSLDIR"; exit 2; }}; '
            f'orig_mean=$(fslstats "$test_img" -m); '
            f'fslmaths "$test_img" -mul 2 {multiplied}; '
            f'mult_mean=$(fslstats {multiplied} -m); '
            f'echo "orig=$orig_mean mult=$mult_mean"',
            timeout=600,
        )
        if code == 2:
            pytest.skip(f"No standard FSL test image found: {output}")
        assert code == 0, f"fslmaths -mul failed: {output}"
        assert os.path.isfile(multiplied), "Multiplied output not created"

        # Parse means from output and verify the ratio
        for line in output.splitlines():
            if line.startswith("orig="):
                parts = line.split()
                try:
                    orig = float(parts[0].split("=")[1])
                    mult = float(parts[1].split("=")[1])
                    if orig != 0:
                        ratio = mult / orig
                        assert abs(ratio - 2.0) < 0.01, (
                            f"Expected mean to double. Original: {orig}, "
                            f"Multiplied: {mult}, Ratio: {ratio}"
                        )
                except (ValueError, IndexError):
                    pass
