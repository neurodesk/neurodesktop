"""Exercise the pinned FreeBrowse integration, including JupyterHub routes."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import httpx
import pytest

from testlib import repo_path


TOKEN = "freebrowse-test-token"
FIXTURES = repo_path("tests/fixtures/freebrowse")


def patched_integration(root):
    (root / "src").mkdir(parents=True)
    package = root / "jupyterlab_freebrowse"
    package.mkdir()
    shutil.copy(FIXTURES / "index.ts", root / "src/index.ts")
    shutil.copy(FIXTURES / "handlers.py", package / "handlers.py")
    spec = importlib.util.spec_from_file_location(
        "patch_freebrowse", repo_path("config/jupyter/patch_freebrowse.py")
    )
    patcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patcher)
    patcher.patch(root)
    return patcher


def test_patch_rejects_changed_upstream(tmp_path):
    patcher = patched_integration(tmp_path)
    with pytest.raises(ValueError, match="upstream seam changed"):
        patcher.patch(tmp_path)


def test_default_factory_and_context_menu_open_encoded_local_files(tmp_path):
    patched_integration(tmp_path)
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const { Signal } = require('@lumino/signaling');
const source = ts.transpileModule(fs.readFileSync(process.argv[1], 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2018 }
}).outputText;
for (const base of ['/', '/user/alice/', 'https://mgh.neurodesk.org/user/stebo85/']) {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {url: 'https://mgh.neurodesk.org'});
  const opened = [], types = [], factories = [], menus = [];
  let selected;
  const commands = new Map();
  class Widget {
    constructor() { this.node = dom.window.document.createElement('div'); this.isDisposed = false; }
    dispose() { this.isDisposed = true; }
  }
  class Factory { constructor(options) { this.options = options; } }
  class DocumentWidget { constructor(options) { Object.assign(this, options); } close() {} }
  const modules = {
    '@jupyterlab/application': {},
    '@jupyterlab/docregistry': { ABCWidgetFactory: Factory, DocumentWidget },
    '@jupyterlab/filebrowser': { IFileBrowserFactory: Symbol() },
    '@jupyterlab/coreutils': { PageConfig: { getBaseUrl: () => base } },
    '@lumino/widgets': { Widget }
  };
  const sandbox = { exports: {}, require: name => modules[name], console,
    document: dom.window.document,
    window: { open: () => { throw Error('Unexpected external browser tab'); } },
    setTimeout: () => { throw Error('Unexpected auto-close'); } };
  vm.runInNewContext(source, sandbox);
  sandbox.exports.default.activate({
    commands: { addCommand: (id, command) => commands.set(id, command),
      execute: (id, args) => { assert.equal(id, 'docmanager:open'); opened.push(args); } },
    contextMenu: { addItem: item => menus.push(item) },
    docRegistry: { addFileType: type => types.push(type), addWidgetFactory: f => factories.push(f) }
  }, { tracker: { currentWidget: { selectedItems: function* () { if (selected) yield selected; } } } });
  assert.deepEqual(Array.from(factories[0].options.defaultFor), ['nifti', 'nifti-gz', 'nvd']);
  assert.equal(factories[0].options.name, 'FreeBrowse');
  assert.equal(menus[0].command, 'freebrowse:open');
  const command = commands.get('freebrowse:open');
  for (const name of ['brain.nii', 'brain.nii.gz', 'scene.nvd', 'Bräin # & ? 100%.NII.GZ', 'scene # &.NVD']) {
    const path = `data folder/${name}`;
    selected = { name, path };
    assert.equal(command.isVisible(), true);
    command.execute();
    const request = opened.pop();
    assert.equal(request.path, path);
    assert.equal(request.factory, 'FreeBrowse');
    const context = { path };
    context.pathChanged = new Signal(context);
    const widget = factories[0].createNewWidget(context);
    const frame = widget.content.node.querySelector('iframe');
    assert.equal(frame.title, 'FreeBrowse');
    for (const value of [frame.src]) {
      const url = new URL(value, 'https://hub.example');
      assert.equal(url.pathname, new URL(`${base}freebrowse/`, 'https://hub.example').pathname);
      assert.equal(url.hash, '');
      const parameter = name.toLowerCase().endsWith('.nvd') ? 'nvd' : 'vol';
      assert.equal(Array.from(url.searchParams).length, 1);
      const file = new URL(url.searchParams.get(parameter), 'https://hub.example');
      assert.equal(decodeURIComponent(file.pathname), new URL(`${base}files/${path.split('/').map(encodeURIComponent).join('/')}`, 'https://hub.example').pathname.split('/').map(decodeURIComponent).join('/'));
      assert.equal(file.search, '');
      assert.equal(file.hash, '');
    }
    const secondContext = { path: 'another.nii' };
    secondContext.pathChanged = new Signal(secondContext);
    const second = factories[0].createNewWidget(secondContext);
    context.path = 'renamed #.nii.gz';
    context.pathChanged.emit(context.path);
    assert.equal(new URL(frame.src).searchParams.get('vol'), `${base}files/renamed%20%23.nii.gz`);
    assert.ok(second.content.node.querySelector('iframe').src.includes('another.nii'));
    widget.content.dispose();
    assert.equal(frame.src, 'about:blank');
    context.path = 'after-close.nii';
    context.pathChanged.emit(context.path);
    assert.equal(frame.src, 'about:blank');
    second.content.dispose();
  }
  selected = { name: 'notes.txt', path: 'notes.txt' };
  assert.equal(command.isVisible(), false);
  selected = undefined;
  assert.equal(command.isVisible(), false);
}
'''
    environment = dict(os.environ)
    environment["NODE_PATH"] = os.environ.get(
        "NEURODESKTOP_LAUNCHER_TEST_NODE_MODULES",
        "/tmp/neurodesk-launcher-tests/node_modules",
    )
    subprocess.run(
        ["node", "-e", script, str(tmp_path / "src/index.ts")],
        env=environment, check=True, capture_output=True, text=True,
    )


@pytest.fixture(scope="module", params=["/", "/user/alice/", "/user/a.b/"])
def viewer_http(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("freebrowse-http")
    patched_integration(root)
    package = root / "jupyterlab_freebrowse"
    (package / "__init__.py").write_text(
        "from .handlers import setup_handlers\n"
        "def _jupyter_server_extension_points():\n"
        "    return [{'module': 'jupyterlab_freebrowse'}]\n"
        "def _load_jupyter_server_extension(app):\n"
        "    setup_handlers(app.web_app)\n"
    )
    assets = package / "static/freebrowse"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<html>FreeBrowse test viewer</html>")
    (assets / "app.js").write_text("// FreeBrowse test asset")
    (root / "secret").write_text("private outside file")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    settings = {
        "ip": "127.0.0.1", "port": port, "port_retries": 0,
        "open_browser": False, "allow_root": True,
        "root_dir": str(root), "base_url": request.param,
        "jpserver_extensions": {"jupyterlab_freebrowse": True},
    }
    script = (
        "import json, sys\n"
        "from jupyter_server.serverapp import ServerApp\n"
        "from traitlets.config import Config\n"
        "app = ServerApp(config=Config({'IdentityProvider': {'token': sys.argv[2]}}), **json.loads(sys.argv[1]))\n"
        "app.initialize([])\napp.start()\n"
    )
    environment = dict(os.environ, PYTHONPATH=str(root),
                       JUPYTER_CONFIG_DIR=str(root / "config"),
                       JUPYTER_RUNTIME_DIR=str(root / "runtime"))
    log_path = root / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", script, json.dumps(settings), TOKEN],
            env=environment, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}{request.param}", timeout=5, trust_env=False) as client:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    assert process.poll() is None, log_path.read_text()
                    try:
                        if client.get("api/status", headers={"Authorization": f"token {TOKEN}"}).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail(log_path.read_text())
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize("credential", [None, "wrong-token"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("endpoint", ["freebrowse/", "freebrowse/app.js"])
def test_assets_require_jupyter_authentication(viewer_http, method, endpoint, credential):
    viewer_http.cookies.clear()
    headers = {"Authorization": f"token {credential}"} if credential else {}
    response = viewer_http.request(method, endpoint, headers=headers)
    assert response.status_code in {302, 403}
    response = viewer_http.request(method, endpoint, headers={"Authorization": f"token {TOKEN}"})
    assert response.status_code == 200
    if method == "GET":
        assert "FreeBrowse test" in response.text


def test_static_route_rejects_traversal(viewer_http):
    response = viewer_http.get("freebrowse/%2e%2e/%2e%2e/secret", headers={"Authorization": f"token {TOKEN}"})
    assert response.status_code in {403, 404}
    assert "private outside file" not in response.text


def test_image_build_replaces_niivue_with_both_freebrowse_bundles():
    dockerfile = repo_path("Dockerfile").read_text()
    assert "jupyterlab-niivue==" not in dockerfile
    assert 'ARG FREEBROWSE_REF="a42a7ea2e6768fccdabbd39813299a099cd586e4"' in dockerfile
    assert "npm run build:jupyter" in dockerfile
    assert "jlpm build:prod" in dockerfile
    assert "jupyter server extension enable jupyterlab_freebrowse --sys-prefix" in dockerfile
    assert "ipyniivue==${IPYNIIVUE_VERSION}" in dockerfile
