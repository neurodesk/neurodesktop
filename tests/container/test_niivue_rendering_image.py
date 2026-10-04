import base64
import io
import json
import os
from pathlib import Path
import subprocess
import time

import nbformat
import numpy as np
import pytest
from PIL import Image

from test_widget_compatibility_image import (
    _stop,
    _unused_port, _wait_for_server, _start_firefox_with_webgl_probe,
    _wait_for_expression, _click_dom_element,
)


@pytest.fixture
def graphics_display(tmp_path, monkeypatch):
    display_number = next(
        number for number in range(177, 277)
        if not Path(f"/tmp/.X11-unix/X{number}").exists()
    )
    log_path = tmp_path / "display.log"
    with log_path.open("w") as log:
        display = subprocess.Popen(
            ["/usr/local/bin/Xtigervnc", f":{display_number}",
             "-geometry", "1280x1024", "-depth", "24", "-SecurityTypes", "None",
             "-rfbport", "-1", "-nolisten", "tcp"],
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 15
            while not Path(f"/tmp/.X11-unix/X{display_number}").exists():
                assert display.poll() is None, log_path.read_text()
                assert time.monotonic() < deadline, log_path.read_text()
                time.sleep(0.1)
            monkeypatch.setenv("DISPLAY", f":{display_number}")
            yield
        finally:
            _stop(display)


@pytest.mark.skipif(
    os.environ.get("NEURODESKTOP_REQUIRE_WEBGL") != "1",
    reason="Run the required graphics acceptance profile to provision a display",
)
def test_niivue_renders_volume_and_synchronizes_interactions(tmp_path, graphics_display):
    port = _unused_port()
    token = "niivue-acceptance"
    source = """import json
import nibabel as nib
import numpy as np
import ipywidgets as widgets
from ipyniivue import NiiVue
from IPython.display import display
volume = np.random.default_rng(0).normal(size=(48, 48, 48)).astype('float32')
nib.save(nib.Nifti1Image(volume, np.eye(4)), 'volume.nii.gz')
viewer = NiiVue(height=400)
viewer.load_volumes([{'path': 'volume.nii.gz'}])
label = widgets.Label(value='crosshair:unread')
read = widgets.Button(description='Read crosshair')
def read_scene(button):
    label.value = 'crosshair:' + json.dumps(list(viewer.scene.crosshair_pos))
read.on_click(read_scene)
display(widgets.VBox([read, label, viewer]))
"""
    notebook = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell(source)],
        metadata={"kernelspec": {"name": "conda-base-py", "language": "python",
                                 "display_name": "Python [conda env:base] *"}},
    )
    nbformat.write(notebook, tmp_path / "volume.ipynb")
    server_log_path = tmp_path / "jupyter.log"
    browser = None
    with server_log_path.open("w") as log:
        server = subprocess.Popen(
            ["/opt/conda/bin/jupyter", "server", "--no-browser", "--LabApp.expose_app_in_browser=True",
             f"--ServerApp.port={port}", "--ServerApp.port_retries=0",
             f"--ServerApp.root_dir={tmp_path}", f"--FileContentsManager.preferred_dir={tmp_path}",
             f"--IdentityProvider.token={token}"],
            env={**os.environ, "HOME": str(tmp_path)}, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_server(f"http://127.0.0.1:{port}/api/status?token={token}", server, server_log_path)
            browser = _start_firefox_with_webgl_probe(tmp_path)
            assert browser.webgl.get("available"), browser.webgl
            bidi, context = browser.bidi, browser.context
            logs = (server_log_path, browser.log_path)
            bidi.request("browsingContext.navigate", {"context": context,
                "url": f"http://127.0.0.1:{port}/lab/tree/volume.ipynb?token={token}", "wait": "complete"})
            _wait_for_expression(bidi, context,
                "document.body.innerText.includes('Python [conda env:base] * | Idle')", log_paths=logs)
            _click_dom_element(bidi, context, "document.querySelector('.jp-Notebook .jp-CodeCell')")
            _click_dom_element(bidi, context,
                "[...document.querySelectorAll('[role=menuitem]')].find(n => n.textContent.trim() === 'Run')")
            _click_dom_element(bidi, context,
                "[...document.querySelectorAll('[role=menuitem]')].find(n => n.textContent.trim().startsWith('Run Selected Cell'))")
            _wait_for_expression(bidi, context,
                "document.querySelectorAll('.jp-OutputArea canvas').length === 1", timeout=60, log_paths=logs)
            bidi.request("browsingContext.setViewport", {"context": context, "viewport": {"width": 1152, "height": 560}})
            canvas = "document.querySelector('.jp-OutputArea canvas')"
            canvas_rectangle = ("JSON.stringify((() => {const c = " + canvas + ";"
                "c.scrollIntoView({block: 'center'}); const r = c.getBoundingClientRect();"
                "return {type:'box', x:r.x, y:r.y, width:r.width, height:r.height};})())")
            rectangle = json.loads(bidi.evaluate(context, canvas_rectangle))
            deadline = time.monotonic() + 30
            while True:
                screenshot = bidi.request("browsingContext.captureScreenshot", {
                    "context": context, "origin": "viewport", "clip": rectangle})
                png = base64.b64decode(screenshot["data"])
                (tmp_path / "niivue-volume.png").write_bytes(png)
                pixels = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
                red, green, blue = pixels.transpose(2, 0, 1)
                grays = red[(red == green) & (green == blue) & (red > 25) & (red < 230)]
                if grays.size > red.size // 4 and np.unique(grays).size > 100:
                    break
                assert time.monotonic() < deadline, (
                    "NiiVue did not render volume intensities", grays.size, np.unique(grays).size)
                time.sleep(0.2)
            label_expression = "[...document.querySelectorAll('.widget-label')].find(n => n.textContent.startsWith('crosshair:')).textContent"
            read_button = "[...document.querySelectorAll('.jupyter-button')].find(n => n.textContent === 'Read crosshair')"
            bidi.evaluate(context, read_button + ".scrollIntoView({block: 'center'}); true")
            _click_dom_element(bidi, context, read_button)
            _wait_for_expression(bidi, context, label_expression + " !== 'crosshair:unread'", log_paths=logs)
            before = bidi.evaluate(context, label_expression)
            assert json.loads(before.split(':', 1)[1]) == [0.5, 0.5, 0.5]
            rectangle = json.loads(bidi.evaluate(context, canvas_rectangle))
            x = round(rectangle["x"] + rectangle["width"] * 0.5)
            y = round(rectangle["y"] + rectangle["height"] * 0.5)
            bidi.request("input.performActions", {"context": context, "actions": [{
                "type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"},
                "actions": [
                    {"type": "pointerMove", "origin": "viewport", "x": x, "y": y},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerMove", "origin": "viewport", "x": x + 25, "y": y + 25, "duration": 300},
                    {"type": "pointerUp", "button": 0},
                ],
            }]})
            bidi.evaluate(context, read_button + ".scrollIntoView({block: 'center'}); true")
            _click_dom_element(bidi, context, read_button)
            _wait_for_expression(bidi, context, label_expression + " !== " + json.dumps(before), log_paths=logs)
            after = json.loads(bidi.evaluate(context, label_expression).split(':', 1)[1])
            assert all(0 <= coordinate <= 1 for coordinate in after), after
            assert max(abs(coordinate - 0.5) for coordinate in after) > 0.05, after
        finally:
            if browser is not None:
                browser.close()
            _stop(server)
