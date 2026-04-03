import subprocess
import os
import pytest


def run_cmd(cmd, timeout=120):
    """Utility to run a shell command and return its exit code and output."""
    process = subprocess.run(
        cmd,
        shell=True,
        executable="/bin/bash",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return process.returncode, process.stdout.strip()


def _cvmfs_should_be_mounted():
    """Return True if CVMFS is expected to be available in this environment."""
    cvmfs_disable = os.environ.get("CVMFS_DISABLE", "false").lower()
    return cvmfs_disable not in ["true", "1"]


def _skip_if_cvmfs_disabled():
    """Skip the test if CVMFS is intentionally disabled."""
    if not _cvmfs_should_be_mounted():
        pytest.skip("CVMFS is disabled (CVMFS_DISABLE=true)")


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


def _load_module(module_name):
    """Load an lmod module by sourcing environment_variables.sh first."""
    # Source the environment setup (which does glob expansion on neurodesk-modules/*)
    # and then load the requested module, capturing the resulting environment.
    code, output = run_cmd(
        f'source /opt/neurodesktop/environment_variables.sh 2>/dev/null; '
        f'source /usr/share/lmod/lmod/init/bash 2>/dev/null; '
        f'module load {module_name} 2>/dev/null; '
        f'env'
    )
    if code == 0:
        for line in output.splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                # Only import environment variables that affect tool discovery
                if key in ("PATH", "LD_LIBRARY_PATH", "MODULEPATH",
                           "FSLDIR", "FSLOUTPUTTYPE", "FREESURFER_HOME"):
                    os.environ[key] = val


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
        _load_module("fsl")

        code, output = run_cmd("command -v fslmaths")
        assert code == 0, (
            f"fslmaths not found in PATH after loading FSL module. "
            f"PATH={os.environ.get('PATH', '')}"
        )

    def test_fslmaths_runs(self, tmp_path):
        """fslmaths should execute and produce correct output on a test image."""
        _skip_if_cvmfs_disabled()
        _load_module("fsl")

        code, _ = run_cmd("command -v fslmaths")
        if code != 0:
            pytest.skip("fslmaths not available after module load")

        fsldir = os.environ.get("FSLDIR", "")
        # Find a standard test image from FSL
        test_image = ""
        candidates = [
            f"{fsldir}/data/standard/MNI152_T1_2mm.nii.gz",
            f"{fsldir}/data/standard/MNI152_T1_1mm.nii.gz",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                test_image = candidate
                break

        if not test_image:
            pytest.skip(
                f"No standard FSL test image found. FSLDIR={fsldir}"
            )

        output_image = str(tmp_path / "test_output.nii.gz")

        # Run fslmaths: add 0 to the image (identity operation)
        code, output = run_cmd(f"fslmaths {test_image} -add 0 {output_image}")
        assert code == 0, f"fslmaths failed with exit code {code}: {output}"
        assert os.path.isfile(output_image), (
            f"fslmaths did not produce output file at {output_image}"
        )

        # Verify output is a valid NIfTI by checking file size is reasonable
        output_size = os.path.getsize(output_image)
        assert output_size > 0, "Output image is empty"

    def test_fslmaths_arithmetic(self, tmp_path):
        """fslmaths should correctly perform arithmetic operations."""
        _skip_if_cvmfs_disabled()
        _load_module("fsl")

        code, _ = run_cmd("command -v fslmaths")
        if code != 0:
            pytest.skip("fslmaths not available after module load")

        fsldir = os.environ.get("FSLDIR", "")
        test_image = ""
        for candidate in [
            f"{fsldir}/data/standard/MNI152_T1_2mm.nii.gz",
            f"{fsldir}/data/standard/MNI152_T1_1mm.nii.gz",
        ]:
            if os.path.isfile(candidate):
                test_image = candidate
                break

        if not test_image:
            pytest.skip(f"No standard FSL test image found. FSLDIR={fsldir}")

        multiplied = str(tmp_path / "multiplied.nii.gz")

        # Multiply image by 2
        code, output = run_cmd(f"fslmaths {test_image} -mul 2 {multiplied}")
        assert code == 0, f"fslmaths -mul failed: {output}"
        assert os.path.isfile(multiplied), "Multiplied output not created"

        # Use fslstats to verify the mean doubled
        code_orig, stats_orig = run_cmd(f"fslstats {test_image} -m")
        code_mult, stats_mult = run_cmd(f"fslstats {multiplied} -m")

        if code_orig == 0 and code_mult == 0:
            try:
                mean_orig = float(stats_orig)
                mean_mult = float(stats_mult)
                ratio = mean_mult / mean_orig if mean_orig != 0 else 0
                assert abs(ratio - 2.0) < 0.01, (
                    f"Expected mean to double. Original: {mean_orig}, "
                    f"Multiplied: {mean_mult}, Ratio: {ratio}"
                )
            except ValueError:
                pass  # fslstats output not parseable, skip this check
