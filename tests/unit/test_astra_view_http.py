import json
import os
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlencode

import httpx
import pytest

from testlib import repo_path


pytest.importorskip("jupyter_server", reason="jupyter-server is not installed; see docs/testing.md")
pytest.importorskip("astra.validation", reason="astra-tools/astra-spec are not installed; see docs/testing.md")

TOKEN = "astra-http-test-token"


@pytest.fixture(scope="module", params=["/", "/user/alice/"])
def viewer_http(request, tmp_path_factory):
    temporary = tmp_path_factory.mktemp("astra-http")
    root = temporary / "workspace"
    root.mkdir()
    shutil.copytree(repo_path("tests/fixtures/astra-bet"), root / "bet")
    outside = temporary / "outside"
    shutil.copytree(root / "bet", outside)
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    settings = {
        "ip": "127.0.0.1", "port": port, "port_retries": 0,
        "open_browser": False, "allow_root": True,
        "root_dir": str(root), "base_url": request.param,
        "jpserver_extensions": {"neurodesk_astra_view.serverext": True},
    }
    script = (
        "import json, sys\n"
        "from jupyter_server.serverapp import ServerApp\n"
        "from traitlets.config import Config\n"
        "config = Config({'IdentityProvider': {'token': sys.argv[2]}})\n"
        "app = ServerApp(config=config, **json.loads(sys.argv[1]))\n"
        "app.initialize([])\n"
        "app.start()\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repo_path("extensions/astra-viewer"))
    environment["JUPYTER_CONFIG_DIR"] = str(temporary / "config")
    environment["JUPYTER_RUNTIME_DIR"] = str(temporary / "runtime")
    log_path = temporary / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", script, json.dumps(settings), TOKEN],
            env=environment, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}{request.param}", timeout=5) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    assert process.poll() is None, log_path.read_text()
                    try:
                        response = client.get("api/status", headers={"Authorization": f"token {TOKEN}"})
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail(log_path.read_text())
                client.cookies.clear()
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("credential", [None, "wrong-token"])
@pytest.mark.parametrize("endpoint", ["graph?spec=bet/astra.yaml", "asset/esm", "asset/css"])
def test_graph_and_assets_deny_missing_or_wrong_tokens(viewer_http, credential, endpoint):
    viewer_http.cookies.clear()
    headers = {"Authorization": f"token {credential}"} if credential else {}
    response = viewer_http.get("neurodesk-astra-view/" + endpoint, headers=headers)
    assert response.status_code in {302, 403}
    assert "boundary_is_inspectable" not in response.text
    assert "export function render" not in response.text


def test_valid_token_serves_the_selected_universe_graph(viewer_http):
    query = urlencode({"spec": "bet/astra.yaml", "universe": "bet/universes/bet-f-0-3.yaml"})
    response = viewer_http.get(
        "neurodesk-astra-view/graph?" + query,
        headers={"Authorization": f"token {TOKEN}"},
    )
    assert response.status_code == 200
    graph = response.json()
    assert graph["errors"] == []
    assert graph["meta"]["universe_id"] == "bet-f-0-3"
    assert graph["trust"]["level"] == "spec-only"
    assert {edge["label"] for edge in graph["edges"] if edge["kind"] == "parameterizes"} == {"f_0_3"}
    assert any(node["id"] == "finding:root/boundary_is_inspectable" for node in graph["nodes"])


@pytest.mark.parametrize("asset,content_type,marker", [
    ("esm", "text/javascript", "export function render({ model, el })"),
    ("css", "text/css", ".astra-viewer {"),
])
def test_valid_token_serves_viewer_assets(viewer_http, asset, content_type, marker):
    response = viewer_http.get(
        "neurodesk-astra-view/asset/" + asset,
        headers={"Authorization": f"token {TOKEN}"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)
    assert response.headers["cache-control"] == "no-cache"
    assert marker in response.text


@pytest.mark.parametrize("parameter", ["spec", "universe", "run"])
@pytest.mark.parametrize("path", ["../outside/astra.yaml", "escape/astra.yaml", "/etc/passwd"])
def test_authenticated_api_rejects_paths_outside_the_workspace(viewer_http, parameter, path):
    query = {"spec": "bet/astra.yaml", parameter: path}
    response = viewer_http.get(
        "neurodesk-astra-view/graph?" + urlencode(query),
        headers={"Authorization": f"token {TOKEN}"},
    )
    assert response.status_code == 404
    assert "nodes" not in response.json()
