"""Open a local NIfTI through the installed default FreeBrowse factory."""

import base64
import io
import json
import os
import subprocess
import time
from urllib.parse import urlsplit, parse_qs

import nibabel as nib
import numpy as np
import pytest
from PIL import Image

from test_niivue_rendering_image import graphics_display
from test_widget_compatibility_image import (
    _stop, _unused_port, _wait_for_server, _start_firefox_with_webgl_probe,
    _wait_for_expression, _click_dom_element,
)


@pytest.mark.skipif(
    os.environ.get("NEURODESKTOP_REQUIRE_WEBGL") != "1",
    reason="Run the graphics acceptance profile to provision a display",
)
@pytest.mark.parametrize("base_path", ["/", "/user/alice/"])
def test_default_freebrowse_opens_and_renders_local_volume(tmp_path, graphics_display, base_path):
    filename = "brain # & ü.nii.gz"
    volume = np.random.default_rng(0).normal(size=(48, 48, 48)).astype("float32")
    nib.save(nib.Nifti1Image(volume, np.eye(4)), tmp_path / filename)
    port = _unused_port()
    token = "freebrowse-acceptance"
    origin = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "jupyter.log"
    browser = None
    with log_path.open("w") as log:
        server = subprocess.Popen(
            ["/opt/conda/bin/jupyter", "server", "--no-browser",
             "--ServerApp.allow_root=True", "--LabApp.expose_app_in_browser=True",
             f"--ServerApp.port={port}", "--ServerApp.port_retries=0",
             f"--ServerApp.base_url={base_path}", f"--ServerApp.root_dir={tmp_path}",
             f"--FileContentsManager.preferred_dir={tmp_path}", f"--IdentityProvider.token={token}"],
            env={**os.environ, "HOME": str(tmp_path),
                 "NBI_TOUR_CONFIG_PATH": "/opt/jovyan_defaults/.jupyter/nbi/tour_config.json"}, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_server(f"{origin}{base_path}api/status?token={token}", server, log_path)
            browser = _start_firefox_with_webgl_probe(tmp_path)
            assert browser.webgl.get("available"), browser.webgl
            bidi, context = browser.bidi, browser.context
            logs = (log_path, browser.log_path)
            bidi.request("browsingContext.navigate", {"context": context,
                "url": f"{origin}{base_path}lab?token={token}", "wait": "complete"})
            _wait_for_expression(bidi, context,
                "Boolean(window.jupyterapp?.commands.hasCommand('freebrowse:open'))", log_paths=logs)
            bidi.evaluate(context, "window.jupyterapp.started.then(() => true)")
            factory = bidi.evaluate(context,
                f"window.jupyterapp.docRegistry.defaultWidgetFactory({json.dumps(filename)}).name")
            assert factory == "FreeBrowse"
            file_node = ("[...document.querySelectorAll('.jp-DirListing-itemText')]"
                         f".find(node => node.textContent === {json.dumps(filename)})")
            _wait_for_expression(bidi, context, f"Boolean({file_node})", log_paths=logs)
            _wait_for_expression(bidi, context,
                f"(() => {{const node = {file_node}; node.scrollIntoView({{block:'center'}});"
                "const r = node.getBoundingClientRect(); return node.closest('.jp-DirListing-item')"
                ".contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));})()",
                log_paths=logs)
            bidi.evaluate(context, "(() => { const open = window.open; window.__opened = [];"
                "window.open = function(...args) { window.__opened.push(args[0]); return open.apply(this, args); }; return true; })()")
            original_tabs = {tab["context"] for tab in
                             bidi.request("browsingContext.getTree", {})["contexts"]}
            point = json.loads(bidi.evaluate(context,
                f"JSON.stringify((() => {{const r = ({file_node}).getBoundingClientRect();"
                "return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};})())"))
            bidi.request("input.performActions", {"context": context, "actions": [{
                "type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "origin": "viewport", **point},
                    {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0},
                    {"type": "pause", "duration": 80},
                    {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0},
                ],
            }]})
            frame = "document.querySelector('iframe[title=\"FreeBrowse\"]')"
            _wait_for_expression(bidi, context, f"Boolean({frame})", log_paths=logs)
            contexts = bidi.request("browsingContext.getTree", {})["contexts"]
            assert {tab["context"] for tab in contexts} == original_tabs, "FreeBrowse opened an external browser tab"
            assert bidi.evaluate(context, "window.__opened.length") == 0
            viewer_url = bidi.evaluate(context, f"{frame}.src")
            query = parse_qs(urlsplit(viewer_url).query)
            assert query["vol"][0].startswith(f"blob:{origin}/")
            assert query["filename"] == [filename]
            assert token not in viewer_url
            canvas = f"{frame}?.contentDocument?.querySelector('canvas')"
            _wait_for_expression(bidi, context, f"Boolean({canvas})", log_paths=logs)
            rectangle = json.loads(bidi.evaluate(context,
                f"JSON.stringify((() => {{ const r = {canvas}.getBoundingClientRect();"
                f"const f = {frame}.getBoundingClientRect();"
                "return {type:'box', x:f.x+r.x, y:f.y+r.y, width:r.width, height:r.height}; })())"))
            deadline = time.monotonic() + 60
            while True:
                screenshot = bidi.request("browsingContext.captureScreenshot", {
                    "context": context, "origin": "viewport", "clip": rectangle})
                png = base64.b64decode(screenshot["data"])
                pixels = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
                red, green, blue = pixels.transpose(2, 0, 1)
                grays = red[(red == green) & (green == blue) & (red > 25) & (red < 230)]
                if grays.size > red.size // 10 and np.unique(grays).size > 50:
                    (tmp_path / "freebrowse-volume.png").write_bytes(png)
                    break
                assert time.monotonic() < deadline, "FreeBrowse did not render the local image"
                time.sleep(0.2)
            _wait_for_expression(bidi, context,
                f"{frame}.contentDocument.body.innerText.includes({json.dumps(filename)})", log_paths=logs)
            _click_dom_element(bidi, context,
                "document.querySelector('.lm-TabBar-tab.lm-mod-current .lm-TabBar-tabCloseIcon')")
            _wait_for_expression(bidi, context, f"!({frame})", log_paths=logs)
        finally:
            if browser is not None:
                browser.close()
            _stop(server)
