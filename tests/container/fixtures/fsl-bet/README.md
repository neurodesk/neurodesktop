# BET anatomical phantom

This independent acceptance notebook creates a 64-cubed NIfTI with 3 mm voxels.
Its brain is a sphere of radius 20 voxels centered at 31.5, with intensity
100 minus radius. A separate skull shell has intensity 30 between radii 23
and 25. These geometric definitions establish the expected anatomy before BET
runs; the oracle does not derive its expectations from BET output.

The central 8-cubed region must survive extraction, at least 98% of the known
inner brain at radius 18 must survive, and the skull/background region at
radius 23 or greater must be excluded. A radius 21.5 envelope permits voxel
sampling at the fitted boundary. Its volume is approximately 1.24 times the
true radius-20 sphere, giving a maximum volume ratio of 1.25. The mask must
also retain at least 85% of the known brain volume. Values in the extracted
brain must match the input under the binary mask.

Run through the maintained client against a FSL-enabled Jupyter server:

```bash
USER_TOKEN=... python scripts/check_fsl_notebook.py \
  --server-url https://server.example/user/tester/ \
  --notebook tests/container/fixtures/fsl-bet/anatomical-phantom.ipynb
```

The default client invocation uses the official FSL course structural MRI
instead. Its general numerical checks require finite, aligned NIfTI images,
a binary nondegenerate mask, and brain values equal to input times mask.

The image suite runs this oracle automatically with
`pytest /opt/tests/test_fsl_bet_image.py`. When
`NEURODESKTOP_REQUIRE_APPLICATIONS=1`, disabling CVMFS fails this check.
