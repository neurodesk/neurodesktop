"""Opt-in verification of CVMFS-backed FSL inside a Kasm session."""

import os
from pathlib import Path
import subprocess

import pytest

from testlib import resolve_source


pytestmark = pytest.mark.skipif(
    os.environ.get("NEURODESKTOP_TEST_KASM_SCIENCE") != "1",
    reason="Enable explicitly in a Kasm container with CVMFS/Apptainer permissions",
)


def test_cvmfs_fsl_doubles_every_voxel(tmp_path):
    import nibabel as nib
    import numpy as np

    environment = resolve_source(
        "/opt/neurodesktop/environment_variables.sh",
        "config/jupyter/environment_variables.sh",
    )
    modules = Path("/cvmfs/neurodesk.ardc.edu.au/neurodesk-modules")
    assert modules.is_dir(), "CVMFS module catalog is not mounted"
    subprocess.run(
        ["findmnt", "--mountpoint", str(modules.parent)], check=True,
    )
    original = np.arange(210, dtype=np.float32).reshape(5, 6, 7)
    input_path = tmp_path / "input.nii.gz"
    output_path = tmp_path / "doubled.nii.gz"
    nib.save(nib.Nifti1Image(original, np.eye(4)), input_path)
    result = subprocess.run(
        [
            "bash", "-ec",
            'source "$1"; source /usr/share/lmod/lmod/init/bash; '
            'module load fsl; module list; '
            'tool=$(command -v fslmaths); '
            'case "$tool" in /cvmfs/*) ;; *) exit 1 ;; esac; '
            'printf "FSL executable: %s\\n" "$tool"; '
            'fslmaths "$2" -mul 2 "$3"',
            "kasm-science", str(environment), str(input_path), str(output_path),
        ],
        check=True, capture_output=True, text=True, timeout=600,
    )
    print(result.stdout, result.stderr)
    actual = nib.load(output_path).get_fdata()
    np.testing.assert_array_equal(actual, original * 2)
    print(f"Verified all {original.size} voxels: FSL output equals input multiplied by 2.")
