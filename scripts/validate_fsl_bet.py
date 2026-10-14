#!/usr/bin/env python3
"""Check BET brain/mask consistency against the input NIfTI image."""
import argparse
import json
import sys

import nibabel as nib
import numpy as np


def validate(input_path, brain_path, mask_path):
    images = [nib.load(str(path)) for path in (input_path, brain_path, mask_path)]
    source, brain, mask = [image.get_fdata() for image in images]
    if len(source.shape) != 3 or any(image.shape != images[0].shape for image in images):
        raise ValueError('BET image shapes differ or are not 3D')
    if any(not np.allclose(image.affine, images[0].affine, rtol=0, atol=1e-5) for image in images):
        raise ValueError('BET image affines differ')
    if any(not np.isfinite(data).all() for data in (source, brain, mask)):
        raise ValueError('BET images contain nonfinite values')
    if not np.isin(mask, [0, 1]).all():
        raise ValueError('BET mask is not binary')
    voxels = int(np.count_nonzero(mask))
    if not 0 < voxels < mask.size:
        raise ValueError('BET mask is empty or includes the entire image')
    if not np.allclose(brain, source * mask, rtol=1e-5, atol=1e-5):
        raise ValueError('BET brain does not equal input multiplied by mask')
    if not np.any(brain != 0):
        raise ValueError('BET brain has no signal')
    return {'mask_voxels': voxels, 'total_voxels': int(mask.size),
            'brain_sum': float(brain.sum()), 'mask_fraction': voxels / mask.size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('brain')
    parser.add_argument('mask')
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.input, args.brain, args.mask), sort_keys=True))
    except (ValueError, OSError):
        print('BET numerical validation failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
