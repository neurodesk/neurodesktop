"""Run the shipped scigent.ai launcher command against each sidecar state."""
import os
from pathlib import Path
import subprocess

import pytest
from testlib import repo_path


def test_parked_sidecar_offers_retry_scigent_and_starting_sidecar_does_not():
    if not os.environ.get('NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES'):
        pytest.fail('Set NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES as described in docs/testing.md')
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('t3_launcher_retry_cases.cjs')),
         str(repo_path('extensions/neurodesk-launcher/src/t3Code.ts'))],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
