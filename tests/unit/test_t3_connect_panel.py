"""Render the shipped Connect panel and check what a failed tunnel tells the user."""
import os
from pathlib import Path
import subprocess

import pytest
from testlib import repo_path


def test_failed_tunnel_offers_the_connections_page_without_trusting_server_text():
    if not os.environ.get('NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES'):
        pytest.fail('Set NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES as described in docs/testing.md')
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('t3_connect_panel_cases.cjs')),
         str(repo_path('extensions/neurodesk-launcher/src/t3Connect.ts'))],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
