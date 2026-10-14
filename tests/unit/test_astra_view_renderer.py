import os
from pathlib import Path
import subprocess

import pytest

from testlib import repo_path


@pytest.mark.parametrize("case", [
    "warnings", "layout", "scroll", "evidence", "trust", "collapsed-warnings", "cleanup",
])
def test_astra_renderer(case):
    if not os.environ.get("NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES"):
        pytest.fail("Set NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES as described in docs/testing.md")
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("astra_view_renderer_cases.mjs")),
         str(repo_path("extensions/astra-viewer/neurodesk_astra_view/static/index.js")), case],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
