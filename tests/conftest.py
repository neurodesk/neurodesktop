"""Make :mod:`testlib` importable from both test tiers.

``tests/unit/`` and ``tests/container/`` are plain directories, and the image
installs the container tier next to this file at ``/opt/tests/``. Putting this
directory on ``sys.path`` explicitly means ``from testlib import ...`` resolves
the same way in every layout.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


import pytest


@pytest.fixture
def image_math_case(tmp_path):
    """A valid, nonzero image and an independent voxel/affine assertion."""
    import nibabel as nib
    import numpy as np

    data = np.arange(1, 28, dtype=np.float32).reshape(3, 3, 3)
    affine = np.diag([2.0, 2.0, 2.0, 1.0])
    source = tmp_path / "input.nii.gz"
    nib.save(nib.Nifti1Image(data, affine), source)

    def check_output(path):
        assert path.is_file(), f"Missing image output: {path}"
        result = nib.load(path)
        np.testing.assert_allclose(result.get_fdata(), data * 2, rtol=1e-6)
        np.testing.assert_allclose(result.affine, affine)

    return source, check_output
