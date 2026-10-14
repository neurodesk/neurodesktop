import json
import os
from pathlib import Path
import subprocess
import sys

import nbformat
import pytest


def test_installed_bet_retains_known_brain_and_excludes_skull(tmp_path):
    if os.environ.get('CVMFS_DISABLE', 'false').lower() in {'true', '1'}:
        assert os.environ.get('NEURODESKTOP_REQUIRE_APPLICATIONS') != '1', (
            'Required FSL acceptance cannot run with CVMFS disabled')
        pytest.skip('CVMFS is explicitly disabled')
    fixture = Path(__file__).parent / 'fixtures/fsl-bet/anatomical-phantom.ipynb'
    assert fixture.is_file(), 'The image must include the BET anatomical oracle'
    output = tmp_path / 'executed.ipynb'
    result = subprocess.run([
        'bash', '-ec',
        'source /opt/neurodesktop/environment_variables.sh; '
        'source /usr/share/lmod/lmod/init/bash; '
        'exec "$1" -m jupyter nbconvert --to notebook --execute '
        '--ExecutePreprocessor.timeout=180 --output executed.ipynb '
        '--output-dir "$2" "$3"',
        'bet-image-acceptance', sys.executable, str(tmp_path), str(fixture),
    ], capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    notebook = nbformat.read(output, as_version=4)
    cells = [cell for cell in notebook['cells'] if cell['cell_type'] == 'code']
    assert [cell['execution_count'] for cell in cells] == [1, 2, 3]
    assert not any(item['output_type'] == 'error' for cell in cells for item in cell['outputs'])
    report = json.loads(''.join(item.get('text', '') for item in cells[-1]['outputs']))
    assert report['foreground_retention'] >= .98
    assert report['outside_fraction'] <= .001
    assert .85 <= report['volume_ratio'] <= 1.25
    print(json.dumps(report, sort_keys=True))
