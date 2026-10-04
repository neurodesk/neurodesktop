import json
from pathlib import Path
import subprocess
import sys

import nibabel as nib
import numpy as np
import pytest

CLI = Path(__file__).resolve().parents[2] / 'scripts/validate_fsl_bet.py'


def validate(tmp_path, brain=None, mask=None, affine=None):
    source = np.arange(1, 9, dtype=np.float32).reshape(2, 2, 2)
    expected_mask = np.array([1, 1, 0, 0, 1, 0, 0, 0], dtype=np.float32).reshape(2, 2, 2)
    expected_brain = np.array([1, 2, 0, 0, 5, 0, 0, 0], dtype=np.float32).reshape(2, 2, 2)
    for name, data in [('input', source), ('brain', expected_brain if brain is None else brain),
                       ('mask', expected_mask if mask is None else mask)]:
        nib.save(nib.Nifti1Image(data, np.eye(4) if name != 'brain' or affine is None else affine),
                 tmp_path / f'{name}.nii.gz')
    return subprocess.run([sys.executable, str(CLI), *[str(tmp_path / f'{n}.nii.gz')
        for n in ('input', 'brain', 'mask')]], capture_output=True, text=True)


def test_worked_brain_and_mask_have_three_voxels_and_sum_eight(tmp_path):
    result = validate(tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['mask_voxels'] == 3
    assert report['brain_sum'] == 8


@pytest.mark.parametrize('change', ['zero-mask', 'full-mask', 'fractional-mask',
    'wrong-brain', 'nan-brain', 'wrong-affine', 'wrong-shape'])
def test_bad_numerical_outputs_fail(tmp_path, change):
    options = {
        'zero-mask': {'mask': np.zeros((2, 2, 2), dtype=np.float32),
                      'brain': np.zeros((2, 2, 2), dtype=np.float32)},
        'full-mask': {'mask': np.ones((2, 2, 2), dtype=np.float32),
                      'brain': np.array([1, 2, 3, 4, 5, 6, 7, 8],
                                        dtype=np.float32).reshape(2, 2, 2)},
        'fractional-mask': {
            'mask': np.array([.5, .5, 0, 0, .5, 0, 0, 0], dtype=np.float32).reshape(2, 2, 2),
            'brain': np.array([.5, 1, 0, 0, 2.5, 0, 0, 0], dtype=np.float32).reshape(2, 2, 2)},
        'wrong-brain': {'brain': np.ones((2, 2, 2), dtype=np.float32)},
        'nan-brain': {'brain': np.full((2, 2, 2), np.nan, dtype=np.float32)},
        'wrong-affine': {'affine': np.diag([2, 1, 1, 1])},
        'wrong-shape': {'brain': np.ones((3, 2, 2), dtype=np.float32)},
    }
    result = validate(tmp_path, **options[change])
    assert result.returncode == 1
    assert 'BET numerical validation failed' in result.stderr
