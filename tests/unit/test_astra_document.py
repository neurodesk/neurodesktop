import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from testlib import repo_path

pytest.importorskip(
    "astra.validation",
    reason="astra-tools/astra-spec are not installed; see docs/testing.md",
)
sys.path.insert(0, str(repo_path("extensions/astra-viewer")))


@pytest.fixture(scope="module")
def document_graph(tmp_path_factory):
    from neurodesk_astra_view import build_graph

    graph = build_graph(
        repo_path("tests/fixtures/astra-bet/astra.yaml"),
        repo_path("tests/fixtures/astra-bet/universes/bet-f-0-5.yaml"),
    )
    target = tmp_path_factory.mktemp("astra-document") / "graph.json"
    target.write_text(json.dumps(graph))
    return target


@pytest.mark.parametrize(
    "case",
    [
        "registry",
        "initial-dispose",
        "discovery",
        "refresh",
        "ambiguity",
        "root-ambiguity",
        "save",
        "retry",
        "stale",
        "dispose",
    ],
)
def test_astra_document_user_actions(case, document_graph):
    assert os.environ.get(
        "NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES"
    ), "See docs/testing.md for DOM dependencies"
    result = subprocess.run(
        [
            "node",
            "--experimental-vm-modules",
            str(Path(__file__).with_name("astra_document_cases.cjs")),
            str(repo_path("extensions/neurodesk-launcher/src/astraViewer.ts")),
            str(
                repo_path(
                    "extensions/astra-viewer/neurodesk_astra_view/static/index.js"
                )
            ),
            str(document_graph),
            case,
        ],
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
