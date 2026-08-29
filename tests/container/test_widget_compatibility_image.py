"""Installed widget-manager compatibility with server-side notebook execution."""

import importlib.metadata
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from dataclasses import dataclass
from pathlib import Path

import nbformat
import pytest
import websocket


WEBGL2_DIAGNOSTICS_EXPRESSION = """JSON.stringify((() => {
    const canvas = document.createElement('canvas');
    let gl = null;
    let error = null;
    try {
        gl = canvas.getContext('webgl2');
    } catch (exception) {
        error = `${exception.name}: ${exception.message}`;
    }
    const debug = gl?.getExtension('WEBGL_debug_renderer_info');
    const diagnostics = {
        available: Boolean(gl),
        contextLost: gl ? gl.isContextLost() : null,
        version: gl ? gl.getParameter(gl.VERSION) : null,
        shadingLanguageVersion: gl
            ? gl.getParameter(gl.SHADING_LANGUAGE_VERSION)
            : null,
        vendor: gl ? gl.getParameter(gl.VENDOR) : null,
        renderer: gl ? gl.getParameter(gl.RENDERER) : null,
        unmaskedVendor: debug
            ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL)
            : null,
        unmaskedRenderer: debug
            ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)
            : null,
        error,
        userAgent: navigator.userAgent,
        hardwareConcurrency: navigator.hardwareConcurrency,
    };
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
    return diagnostics;
})())"""

DOM_DIAGNOSTICS_EXPRESSION = (
    "JSON.stringify({"
    "body: document.body.innerText.slice(-2000),"
    "outputs: [...document.querySelectorAll('.jp-OutputArea-output')]"
    ".map(node => node.outerHTML.slice(0, 2000))"
    "})"
)

IPYNIIVUE_INTERVAL_PROBE_PRELOAD = """() => {
    const records = [];
    Object.defineProperty(
        window,
        '__neurodesktopIpyniivueIntervalActivity',
        {value: records, configurable: false},
    );
    const nativeSetInterval = window.setInterval;
    window.setInterval = function(callback, delay, ...args) {
        const stack = new Error().stack || '';
        if (!stack.includes('neurodesktop-ipyniivue')) {
            return nativeSetInterval.call(this, callback, delay, ...args);
        }
        const record = {delay: Number(delay), calls: 0};
        records.push(record);
        return nativeSetInterval.call(
            this,
            (...callbackArgs) => {
                record.calls += 1;
                return callback(...callbackArgs);
            },
            delay,
            ...args,
        );
    };
}"""

IPYNIIVUE_INTERVAL_SNAPSHOT_EXPRESSION = (
    "JSON.stringify("
    "window.__neurodesktopIpyniivueIntervalActivity || []"
    ")"
)

SCENE_SYNC_COUNT_EXPRESSION = (
    "Number((([...document.querySelectorAll('.widget-label')].find("
    "node => node.textContent.startsWith('scene-sync-count:')"
    ")?.textContent || 'scene-sync-count:-1').split(':').at(-1)))"
)


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_server(
    url: str, process: subprocess.Popen[str], log_path: Path
) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(
                "Jupyter Server exited before serving the widget notebook:\n"
                + log_path.read_text(encoding="utf-8")
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    pytest.fail("Jupyter Server did not become ready")


class _BidiSession:
    def __init__(self, url: str, firefox: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 30
        while True:
            if firefox.poll() is not None:
                pytest.fail("Firefox exited before opening its WebDriver BiDi endpoint")
            try:
                self.websocket = websocket.create_connection(
                    url,
                    timeout=1,
                    suppress_origin=True,
                )
                self.websocket.settimeout(30)
                break
            except (OSError, websocket.WebSocketException):
                if time.monotonic() >= deadline:
                    pytest.fail("Firefox did not open its WebDriver BiDi endpoint")
                time.sleep(0.1)
        self._next_id = 1
        self.events: list[dict] = []

    def request(self, method: str, params: dict) -> dict:
        request_id = self._next_id
        self._next_id += 1
        self.websocket.send(
            json.dumps({"id": request_id, "method": method, "params": params})
        )
        while True:
            message = json.loads(self.websocket.recv())
            if message.get("id") != request_id:
                self.events.append(message)
                continue
            if message.get("type") == "error":
                pytest.fail(f"WebDriver BiDi {method} failed: {message}")
            return message["result"]

    def evaluate(self, context: str, expression: str):
        result = self.request(
            "script.evaluate",
            {
                "expression": expression,
                "target": {"context": context},
                "awaitPromise": True,
            },
        )
        return result["result"].get("value")

    def close(self) -> None:
        self.websocket.close()


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _firefox_environment(home: Path) -> dict[str, str]:
    """Return an isolated environment without a forced Mesa driver mode."""
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    # Firefox's headless compositor chooses a working GL path itself. Forcing
    # Mesa software rendering makes SWGL fail to map its default framebuffer
    # on the CI container's displayless process.
    environment.pop("LIBGL_ALWAYS_SOFTWARE", None)
    return environment


def _write_firefox_profile(profile: Path) -> None:
    """Keep WebGL enabled even when a disposable CI runner is blocklisted."""
    (profile / "user.js").write_text(
        'user_pref("webgl.disabled", false);\n'
        'user_pref("webgl.enable-webgl2", true);\n'
        'user_pref("webgl.forbid-software", false);\n'
        'user_pref("webgl.force-enabled", true);\n',
        encoding="utf-8",
    )


def _tail_text(path: Path, limit: int = 8_000) -> str:
    if not path.exists():
        return "<missing>"
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


def _probe_webgl2(bidi: _BidiSession, context: str) -> dict:
    result = bidi.evaluate(context, WEBGL2_DIAGNOSTICS_EXPRESSION)
    if not isinstance(result, str):
        return {"available": False, "probeResult": result}
    try:
        decoded = json.loads(result)
    except json.JSONDecodeError:
        return {"available": False, "probeResult": result}
    if not isinstance(decoded, dict):
        return {"available": False, "probeResult": decoded}
    return decoded


def _browser_failure_diagnostics(
    bidi: _BidiSession,
    context: str,
    *,
    log_paths: tuple[Path, ...] = (),
) -> dict:
    browser_errors = [
        event["params"]["text"]
        for event in bidi.events
        if event.get("method") == "log.entryAdded"
        and event["params"].get("level") == "error"
    ]
    state_result = bidi.evaluate(context, DOM_DIAGNOSTICS_EXPRESSION)
    try:
        state = (
            json.loads(state_result)
            if isinstance(state_result, str)
            else state_result
        )
    except json.JSONDecodeError:
        state = state_result
    return {
        "browserErrors": browser_errors,
        "webgl": _probe_webgl2(bidi, context),
        "state": state,
        "processLogs": {str(path): _tail_text(path) for path in log_paths},
    }


@dataclass
class _FirefoxBrowser:
    process: subprocess.Popen[str]
    bidi: _BidiSession
    log: object
    log_path: Path
    context: str
    webgl: dict
    startup_failures: list[dict]

    def close(self) -> None:
        try:
            self.bidi.close()
        finally:
            try:
                _stop(self.process)
            finally:
                self.log.close()


def _start_firefox_with_webgl_probe(
    tmp_path: Path,
    *,
    attempts: int = 3,
) -> _FirefoxBrowser:
    """Start Firefox, replacing only processes that lack WebGL2 at startup."""
    startup_failures = []
    for attempt in range(1, attempts + 1):
        firefox_port = _unused_port()
        firefox_home = tmp_path / f"firefox-home-{attempt}"
        firefox_profile = tmp_path / f"firefox-profile-{attempt}"
        firefox_log_path = tmp_path / f"firefox-{attempt}.log"
        firefox_home.mkdir()
        firefox_profile.mkdir()
        _write_firefox_profile(firefox_profile)
        firefox_log = firefox_log_path.open("w", encoding="utf-8")
        firefox = subprocess.Popen(
            [
                "/usr/bin/firefox",
                "--headless",
                "--profile",
                str(firefox_profile),
                f"--remote-debugging-port={firefox_port}",
                "about:blank",
            ],
            env=_firefox_environment(firefox_home),
            stdout=firefox_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        bidi = None
        try:
            bidi = _BidiSession(f"ws://127.0.0.1:{firefox_port}/session", firefox)
            bidi.request("session.new", {"capabilities": {}})
            bidi.request("session.subscribe", {"events": ["log.entryAdded"]})
            context = bidi.request("browsingContext.create", {"type": "tab"})[
                "context"
            ]
            webgl = _probe_webgl2(bidi, context)
            startup_failure = {
                "attempt": attempt,
                "webgl": webgl,
                "firefoxLog": _tail_text(firefox_log_path),
            }
            if webgl.get("available") or attempt == attempts:
                if not webgl.get("available"):
                    startup_failures.append(startup_failure)
                return _FirefoxBrowser(
                    process=firefox,
                    bidi=bidi,
                    log=firefox_log,
                    log_path=firefox_log_path,
                    context=context,
                    webgl=webgl,
                    startup_failures=startup_failures,
                )
            startup_failures.append(startup_failure)
        except BaseException:
            if bidi is not None:
                bidi.close()
            _stop(firefox)
            firefox_log.close()
            raise
        bidi.close()
        _stop(firefox)
        firefox_log.close()


def _click_dom_element(
    bidi: _BidiSession,
    context: str,
    element_expression: str,
    *,
    x_fraction: float = 0.5,
    y_fraction: float = 0.5,
) -> None:
    point = json.loads(
        bidi.evaluate(
            context,
            "JSON.stringify((() => {"
            f"const node = {element_expression};"
            "if (!node) return null;"
            "const rect = node.getBoundingClientRect();"
            f"return {{x: Math.round(rect.left + rect.width * {x_fraction}),"
            f"y: Math.round(rect.top + rect.height * {y_fraction})}};"
            "})())",
        )
    )
    assert point is not None
    bidi.request(
        "input.performActions",
        {
            "context": context,
            "actions": [
                {
                    "type": "pointer",
                    "id": "mouse",
                    "parameters": {"pointerType": "mouse"},
                    "actions": [
                        {
                            "type": "pointerMove",
                            "duration": 0,
                            "origin": "viewport",
                            "x": point["x"],
                            "y": point["y"],
                        },
                        {"type": "pointerDown", "button": 0},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ],
        },
    )


def _wait_for_expression(
    bidi: _BidiSession,
    context: str,
    expression: str,
    *,
    timeout: float = 30,
    log_paths: tuple[Path, ...] = (),
) -> object:
    deadline = time.monotonic() + timeout
    last_value = None
    while time.monotonic() < deadline:
        last_value = bidi.evaluate(context, expression)
        if last_value:
            return last_value
        time.sleep(0.1)
    diagnostics = _browser_failure_diagnostics(
        bidi,
        context,
        log_paths=log_paths,
    )
    pytest.fail(
        f"Browser condition did not become true; last value was {last_value!r}.\n"
        + json.dumps(diagnostics, indent=2, sort_keys=True)
    )


def _widget_regression_notebook(*, include_niivue: bool):
    niivue_imports = ""
    niivue_setup = ""
    niivue_display = ""
    if include_niivue:
        niivue_imports = (
            "import nibabel\n"
            "import numpy as np\n"
            "from ipyniivue import NiiVue\n"
        )
        niivue_setup = (
            "volume = np.random.default_rng(0).normal(\n"
            "    size=(48, 48, 48)).astype('float32')\n"
            "nibabel.save(nibabel.Nifti1Image(volume, np.eye(4)),\n"
            "             'volume.nii.gz')\n"
        )
        niivue_display = (
            "niivues = [NiiVue(height=128) for _ in range(9)]\n"
            "niivues[0].load_volumes([{'path': 'volume.nii.gz'}])\n"
            "scene_sync_count = 0\n"
            "scene_sync_label = widgets.Label(value='scene-sync-count:0')\n"
            "def record_scene_sync(change):\n"
            "    global scene_sync_count\n"
            "    scene_sync_count += 1\n"
            "    scene_sync_label.value = (\n"
            "        f'scene-sync-count:{scene_sync_count}')\n"
            "for niivue in niivues:\n"
            "    niivue.observe(record_scene_sync, names='scene')\n"
            "display(widgets.VBox([scene_sync_label, *niivues]))"
        )

    source = (
        "import asyncio\n"
        "import sys\n"
        "import ipywidgets as widgets\n"
        f"{niivue_imports}"
        "from IPython.display import display\n"
        f"{niivue_setup}"
        "widget_run = globals().get('_widget_regression_run', 0) + 1\n"
        "_widget_regression_run = widget_run\n"
        "print(f'stream-run-{widget_run}')\n"
        "for fragment in range(20):\n"
        "    sys.stdout.write(f'\\rstream-fragment-{fragment:02d}')\n"
        "    sys.stdout.flush()\n"
        "    await asyncio.sleep(0.05)\n"
        "print()\n"
        "print('stream-end')\n"
        "model_id = f'delayed-hbox-model-{widget_run}'\n"
        "async def open_model_later():\n"
        "    await asyncio.sleep(3)\n"
        "    return widgets.HBox([\n"
        "        widgets.IntSlider(value=42),\n"
        "        widgets.Label(value='nested'),\n"
        "    ], model_id=model_id)\n"
        "delayed_model_task = asyncio.create_task(open_model_later())\n"
        "display({\n"
        "    'application/vnd.jupyter.widget-view+json': {\n"
        "        'version_major': 2,\n"
        "        'version_minor': 0,\n"
        "        'model_id': model_id,\n"
        "    }\n"
        "}, raw=True)\n"
        f"{niivue_display}"
    )
    return nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_code_cell(source),
            nbformat.v4.new_markdown_cell("Widget regression end."),
        ],
        metadata={
            "kernelspec": {
                "display_name": "Python [conda env:base] *",
                "language": "python",
                "name": "conda-base-py",
            }
        },
    )


def test_ipyniivue_interval_probe_records_matching_asset_timer(tmp_path: Path) -> None:
    """The browser probe detects a timer whose source is an ipyniivue asset."""
    browser = _start_firefox_with_webgl_probe(tmp_path)
    try:
        browser.bidi.request(
            "script.addPreloadScript",
            {
                "functionDeclaration": IPYNIIVUE_INTERVAL_PROBE_PRELOAD,
                "contexts": [browser.context],
            },
        )
        script = (
            "setInterval(() => {}, 30);\n"
            "//# sourceURL=neurodesktop-ipyniivue-probe.js"
        )
        document = f"<script>eval({json.dumps(script)})</script>"
        browser.bidi.request(
            "browsingContext.navigate",
            {
                "context": browser.context,
                "url": "data:text/html," + urllib.parse.quote(document),
                "wait": "complete",
            },
        )
        time.sleep(0.15)
        records = json.loads(
            browser.bidi.evaluate(
                browser.context,
                IPYNIIVUE_INTERVAL_SNAPSHOT_EXPRESSION,
            )
        )
        assert len(records) == 1
        assert records[0]["delay"] == 30
        assert records[0]["calls"] > 0
    finally:
        browser.close()


def test_widget_manager_waits_for_a_late_model_registration():
    """Yjs output may arrive before the matching kernel ``comm_open``.

    ``jupyter-server-documents`` delivers notebook output over a different
    websocket from widget comms.  The shipped frontend must therefore retry a
    missing model briefly instead of permanently rendering ``model not found``
    (or waiting forever at ``Loading widget...``).
    """
    assert importlib.metadata.version("ipykernel") == "6.31.0"
    assert importlib.metadata.version("ipywidgets") == "8.1.9"
    assert importlib.metadata.version("jupyterlab_widgets") == "3.0.17"

    labextension = (
        Path(sys.prefix)
        / "share/jupyter/labextensions/@jupyter-widgets/jupyterlab-manager"
    )
    package = json.loads((labextension / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == "5.0.16"

    bundles = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((labextension / "static").glob("*.js"))
    )
    late_model_retry = re.compile(
        r"async get_model\([^)]*\).*?Date\.now\(\).*?"
        r"setTimeout\([^,]+,100\).*?widget model not found",
        re.DOTALL,
    )
    assert late_model_retry.search(bundles), (
        "the installed widget manager still fails immediately when a widget "
        "view reaches the browser before its model"
    )
    assert "neurodesktop-widget-model-retry" in bundles
    assert "Date.now()-o<1e4" in bundles


def test_server_documents_installs_reconnect_data_loss_guards() -> None:
    """Both halves of the blank-notebook reconnect fix are active assets."""
    import jupyter_server_documents

    package_dir = Path(jupyter_server_documents.__file__).parent
    yroom_source = (package_dir / "rooms/yroom.py").read_text(encoding="utf-8")
    assert "neurodesktop-late-sync-step2" in yroom_source
    assert "handshake_timeout = traitlets.Float(" in yroom_source
    assert "self._pending_ss2: dict[str, asyncio.Future[bytes]] = {}" in yroom_source
    assert "self._pending_ss2.pop(client_id, None)" in yroom_source

    labextension = (
        Path(sys.prefix)
        / "share/jupyter/labextensions/@jupyter-ai-contrib/server-documents"
    )
    package = json.loads((labextension / "package.json").read_text(encoding="utf-8"))
    remote_entry = labextension / package["jupyterlab"]["_build"]["load"]
    remote_source = remote_entry.read_text(encoding="utf-8")
    assert "neurodesktop-widget-cache-safe-entry" in remote_source

    active_bundles = [
        path
        for path in (labextension / "static").glob("*.js")
        if re.search(r"\.([0-9a-f]{20})\.js$", path.name)
        and re.search(r"\.([0-9a-f]{20})\.js$", path.name).group(1)
        in remote_source
        and "neurodesktop-idempotent-divergent-repair"
        in path.read_text(encoding="utf-8")
    ]
    assert len(active_bundles) == 1
    active_source = active_bundles[0].read_text(encoding="utf-8")
    assert "p.decodeStateVector(s)" in active_source
    assert "o.id.client" in active_source
    assert "o.id.clock" in active_source


def test_late_sync_step2_is_applied_without_disconnect() -> None:
    """A timeout resumes broadcasts, then the late CRDT reply is applied."""
    import asyncio
    import logging

    from jupyter_server_documents.rooms.yroom import YRoom
    from pycrdt import YMessageType, YSyncMessageType

    class Clients:
        def __init__(self) -> None:
            self.removed: list[str] = []

        def mark_desynced(self, client_id: str) -> None:
            pass

        def remove(self, client_id: str) -> None:
            self.removed.append(client_id)

    class UpdateChannel:
        def __init__(self) -> None:
            self.paused = False

        def pause(self) -> None:
            self.paused = True

        def resume(self, *, pre_sync_sv: bytes) -> None:
            self.paused = False

    class YDoc:
        def get_state(self) -> bytes:
            return b"\x00"

    class StubRoom:
        handle_sync = YRoom.handle_sync
        handle_message = YRoom.handle_message
        room_id = "late-ss2-room"
        handshake_timeout = 0.01
        log = logging.getLogger("late-ss2-room")

        def __init__(self) -> None:
            self.clients = Clients()
            self.update_channel = UpdateChannel()
            self._ydoc = YDoc()
            self._pending_ss2: dict[str, asyncio.Future[bytes]] = {}
            self.applied: list[tuple[str, bytes]] = []

        def _has_divergent_history(self, message: bytes, state: bytes) -> bool:
            return False

        def handle_sync_step1(self, client_id: str, message: bytes) -> None:
            pass

        def handle_sync_step2(self, client_id: str, message: bytes) -> None:
            self.applied.append((client_id, message))

    async def run() -> StubRoom:
        room = StubRoom()
        await room.handle_sync("stale-client", b"\x00\x00")
        assert not room.update_channel.paused
        assert not room.clients.removed
        assert not room._pending_ss2

        late_reply = bytes(
            [YMessageType.SYNC, YSyncMessageType.SYNC_STEP2, 0]
        )
        await room.handle_message("stale-client", late_reply)
        assert room.applied == [("stale-client", late_reply)]
        return room

    asyncio.run(run())


def test_ipyniivue_uses_one_shared_bundle_with_per_model_state() -> None:
    """Models share code, own state, and synchronize scenes without polling."""
    import ipyniivue

    assert importlib.metadata.version("ipyniivue") == "2.4.4"
    package_dir = Path(ipyniivue.__file__).parent
    bootstrap = (package_dir / "static/widget.js").read_text(encoding="utf-8")
    assert len(bootstrap) < 2_000
    asset_names = re.findall(
        r"neurodesktop-ipyniivue\.[0-9a-f]{20}\.js", bootstrap
    )
    assert len(asset_names) == 1

    shared_bundle = Path(sys.prefix) / "share/jupyter/lab/static" / asset_names[0]
    shared_source = shared_bundle.read_text(encoding="utf-8")
    assert "function createWidgetDefinition(){let vA;" in shared_source
    assert "neurodesktop-ipyniivue-model-cleanup" in shared_source
    assert "neurodesktop-ipyniivue-event-scene-sync" in shared_source
    assert "setInterval(" not in shared_source
    assert 'getExtension("WEBGL_lose_context")?.loseContext()' in shared_source


def test_server_side_stream_fragments_are_one_replay_safe_crdt_output() -> None:
    """The notebook room stores one processed output per contiguous stream."""
    from jupyter_server_documents.outputs.output_processor import OutputProcessor
    from pycrdt import Array, Doc, Map, Text

    document = Doc({"cell": Map({"outputs": Array()})})
    cell = document["cell"]
    processor = OutputProcessor()
    processor.use_outputs_service = False

    def write(text: str) -> None:
        processor.process_output(
            "stream",
            cell,
            file_id=None,
            cell_id="stream-cell",
            content={"name": "stdout", "text": text},
        )

    write("stream-run-1\n")
    for fragment in range(20):
        write(f"\rstream-fragment-{fragment:02d}")
    write("\n")
    write("stream-end\n")

    outputs = cell["outputs"]
    assert len(outputs) == 1
    assert isinstance(outputs[0], Map)
    assert isinstance(outputs[0]["text"], Text)
    assert str(outputs[0]["text"]) == (
        "stream-run-1\nstream-fragment-19\nstream-end\n"
    )

    # A cleared re-execution starts with a fresh cursor and still produces one
    # output. This mirrors YNotebookRoom clearing the array before execution.
    del outputs[:]
    write("stream-run-2\n")
    write("abc\rX")
    write("Y\n")
    write("stream-end\n")
    assert len(outputs) == 1
    assert str(outputs[0]["text"]) == "stream-run-2\nXYc\nstream-end\n"

    # A joining client must reconstruct one complete stream map. Multiple
    # adjacent maps trigger JupyterLab's local combine-and-echo replay bug.
    replay = Doc()
    # Register the shared root without creating competing local cell contents.
    replay_cell = replay.get("cell", type=Map)
    replay.apply_update(document.get_update())
    replay_outputs = replay_cell["outputs"]
    assert len(replay_outputs) == 1
    assert str(replay_outputs[0]["text"]) == "stream-run-2\nXYc\nstream-end\n"

    # The coalescing rules live in the parity module the patcher installs
    # into the package; the processor only delegates to it.
    from jupyter_server_documents.outputs import _neurodesktop_stream

    assert hasattr(_neurodesktop_stream, "write_stream_output")

    # Backspaces follow JupyterLab's cursor rules, and interleaved stream
    # names stay separate outputs exactly as nbformat records them.
    del outputs[:]
    write("abc")
    write("\b\bXY\n")
    processor.process_output(
        "stream",
        cell,
        file_id=None,
        cell_id="stream-cell",
        content={"name": "stderr", "text": "err-1\n"},
    )
    write("out-2\n")
    assert [output["name"] for output in outputs] == ["stdout", "stderr", "stdout"]
    assert [str(output["text"]) for output in outputs] == [
        "aXY\n",
        "err-1\n",
        "out-2\n",
    ]


def test_room_queue_survives_a_rejected_message() -> None:
    """One raising message handler must not stop the room's queue task.

    Without the queue guard the exception escapes the background task, so
    later messages are never handled and ``Queue.join()`` deadlocks — this
    test then fails by timeout.
    """
    import asyncio
    import logging

    from jupyter_server_documents.rooms.yroom import YRoom

    class StubRoom:
        _process_message_queue = YRoom._process_message_queue
        room_id = "queue-guard-room"
        log = logging.getLogger("queue-guard-room")
        file_api = None

        def __init__(self) -> None:
            self._message_queue = asyncio.Queue()
            self.handled: list[str] = []

        async def handle_message(self, client_id: str, message: bytes) -> None:
            if client_id == "poison":
                raise RuntimeError("rejected frame")
            self.handled.append(client_id)

    async def run() -> list[str]:
        room = StubRoom()
        room._message_queue.put_nowait(("poison", b""))
        room._message_queue.put_nowait(("late", b""))
        task = asyncio.create_task(room._process_message_queue())
        await asyncio.wait_for(room._message_queue.join(), timeout=10)
        room._message_queue.put_nowait(None)
        await asyncio.wait_for(task, timeout=10)
        return room.handled

    assert asyncio.run(run()) == ["late"]


def test_server_side_execution_renders_streams_and_widgets(tmp_path: Path) -> None:
    """Streams and delayed widgets replay, with NiiVue when WebGL2 works."""
    server_port = _unused_port()
    token = "widget-browser-regression"
    server_log_path = tmp_path / "jupyter-server.log"
    server_log = server_log_path.open("w", encoding="utf-8")
    server_home = tmp_path / "server-home"
    server_home.mkdir()
    server_env = os.environ.copy()
    server_env["HOME"] = str(server_home)
    server = subprocess.Popen(
        [
            "/opt/conda/bin/jupyter",
            "server",
            "--no-browser",
            "--ServerApp.allow_root=True",
            f"--ServerApp.port={server_port}",
            "--ServerApp.port_retries=0",
            f"--ServerApp.root_dir={tmp_path}",
            f"--FileContentsManager.preferred_dir={tmp_path}",
            f"--IdentityProvider.token={token}",
        ],
        stdout=server_log,
        stderr=subprocess.STDOUT,
        env=server_env,
        text=True,
    )

    browser = None
    try:
        _wait_for_server(
            f"http://127.0.0.1:{server_port}/api/status?token={token}",
            server,
            server_log_path,
        )
        browser = _start_firefox_with_webgl_probe(tmp_path)
        bidi = browser.bidi
        context = browser.context
        diagnostic_logs = (browser.log_path, server_log_path)
        include_niivue = bool(browser.webgl.get("available"))
        expected_widget_boxes = 2 if include_niivue else 1
        expected_niivue_canvases = 9 if include_niivue else 0
        render_condition = (
            "document.querySelectorAll('.jupyter-widgets.widget-box').length "
            f"=== {expected_widget_boxes} && "
            "document.querySelectorAll('.jp-OutputArea canvas').length "
            f"=== {expected_niivue_canvases}"
        )
        if not include_niivue:
            warnings.warn(
                "WebGL2 is unavailable; running stream and delayed-widget "
                "replay without NiiVue. "
                + json.dumps(
                    {
                        "webgl": browser.webgl,
                        "startupFailures": [
                            {
                                "attempt": failure["attempt"],
                                "webgl": failure["webgl"],
                            }
                            for failure in browser.startup_failures
                        ],
                        "firefoxLog": _tail_text(browser.log_path),
                    },
                    sort_keys=True,
                ),
                RuntimeWarning,
                stacklevel=1,
            )
        notebook = _widget_regression_notebook(include_niivue=include_niivue)
        nbformat.write(notebook, tmp_path / "widget.ipynb")
        if include_niivue:
            bidi.request(
                "script.addPreloadScript",
                {
                    "functionDeclaration": IPYNIIVUE_INTERVAL_PROBE_PRELOAD,
                    "contexts": [context],
                },
            )
        bidi.request(
            "browsingContext.navigate",
            {
                "context": context,
                "url": (
                    f"http://127.0.0.1:{server_port}/lab/tree/widget.ipynb"
                    f"?token={token}"
                ),
                "wait": "complete",
            },
        )
        _wait_for_expression(
            bidi,
            context,
            "Boolean(document.querySelector('.jp-Notebook .jp-CodeCell'))",
            log_paths=diagnostic_logs,
        )
        _wait_for_expression(
            bidi,
            context,
            "document.body.innerText.includes("
            "'Python [conda env:base] * | Idle'"
            ")",
            log_paths=diagnostic_logs,
        )
        # The shared ipyniivue bundle is imported after Run below; raise the
        # resource-timing buffer first so its fetch cannot be evicted before
        # the single-fetch assertion reads it.
        assert bidi.evaluate(
            context,
            "performance.setResourceTimingBufferSize(5000); true",
        )
        assert bidi.evaluate(
            context,
            "document.querySelector('.jp-Notebook .jp-CodeCell').click(); true",
        )
        _click_dom_element(
            bidi,
            context,
            "[...document.querySelectorAll('[role=menuitem]')]"
            ".find(node => node.textContent.trim() === 'Run')",
        )
        _wait_for_expression(
            bidi,
            context,
            "[...document.querySelectorAll('[role=menuitem]')]"
            ".some(node => node.textContent.trim().startsWith("
            "'Run Selected Cell') && !node.textContent.includes('and'))",
            log_paths=diagnostic_logs,
        )
        _click_dom_element(
            bidi,
            context,
            "[...document.querySelectorAll('[role=menuitem]')]"
            ".find(node => node.textContent.trim().startsWith("
            "'Run Selected Cell') && !node.textContent.includes('and'))",
        )
        _wait_for_expression(
            bidi,
            context,
            f"Boolean(({render_condition}) || "
            "document.body.innerText.includes('model not found') || "
            "document.querySelector("
            "'.jp-OutputArea-output[data-mime-type=\"text/plain\"]'"
            ")"
            ")",
            timeout=45,
            log_paths=diagnostic_logs,
        )
        expected_stream = "stream-run-1\nstream-fragment-19\nstream-end\n"
        output = json.loads(
            bidi.evaluate(
                context,
                "JSON.stringify((() => {"
                "return {"
                "modelError: document.body.innerText.includes("
                "'model not found'"
                "),"
                "loadingWidget: document.body.innerText.includes("
                "'Loading widget'"
                "),"
                "plainTextFallback: [...document.querySelectorAll("
                "'.jp-OutputArea-output[data-mime-type=\"text/plain\"]'"
                ")].some(node => node.textContent.includes('VBox(')),"
                "streamText: document.body.innerText.includes("
                "'stream-fragment-19'),"
                "streamOutputs: [...document.querySelectorAll("
                "'.jp-OutputArea-output[data-mime-type=\"application/vnd.jupyter.stdout\"]'"
                ")].map(node => node.textContent),"
                "widgetBoxes: document.querySelectorAll("
                "'.jupyter-widgets.widget-box'"
                ").length,"
                "niivueCanvases: document.querySelectorAll("
                "'.jp-OutputArea canvas'"
                ").length"
                "};"
                "})())",
            )
        )
        assert output == {
            "modelError": False,
            "loadingWidget": False,
            "plainTextFallback": False,
            "streamText": True,
            "streamOutputs": [expected_stream],
            "widgetBoxes": expected_widget_boxes,
            "niivueCanvases": expected_niivue_canvases,
        }

        # Nine models must share one fetched bundle. Per-model delivery is
        # the 5 MB-per-widget regression the ipyniivue workaround removes.
        if include_niivue:
            shared_bundle_fetches = json.loads(
                bidi.evaluate(
                    context,
                    "JSON.stringify(performance.getEntriesByType('resource')"
                    ".map(entry => entry.name)"
                    ".filter(name => name.includes('neurodesktop-ipyniivue')))",
                )
            )
            assert len(shared_bundle_fetches) == 1, shared_bundle_fetches

            # Exercise each viewer's real NiiVue.sync() path, then require
            # both model traffic and frontend work to become quiescent. The
            # upstream 2.4.4 bundle keeps one 30 ms setInterval per model;
            # the preload probe records callbacks created by that asset.
            for canvas_index in range(expected_niivue_canvases):
                canvas_expression = (
                    "document.querySelectorAll('.jp-OutputArea canvas')"
                    f"[{canvas_index}]"
                )
                assert bidi.evaluate(
                    context,
                    "(() => {"
                    f"const canvas = {canvas_expression};"
                    "canvas.scrollIntoView({block: 'center'});"
                    "return true;"
                    "})()",
                )
                _click_dom_element(
                    bidi,
                    context,
                    canvas_expression,
                    x_fraction=0.25,
                    y_fraction=0.25,
                )

            _wait_for_expression(
                bidi,
                context,
                f"{SCENE_SYNC_COUNT_EXPRESSION} >= {expected_niivue_canvases}",
                timeout=15,
                log_paths=diagnostic_logs,
            )
            time.sleep(1)
            scene_sync_count = bidi.evaluate(
                context,
                SCENE_SYNC_COUNT_EXPRESSION,
            )
            interval_activity = json.loads(
                bidi.evaluate(
                    context,
                    IPYNIIVUE_INTERVAL_SNAPSHOT_EXPRESSION,
                )
            )
            time.sleep(1)
            idle_scene_sync_count = bidi.evaluate(
                context,
                SCENE_SYNC_COUNT_EXPRESSION,
            )
            idle_interval_activity = json.loads(
                bidi.evaluate(
                    context,
                    IPYNIIVUE_INTERVAL_SNAPSHOT_EXPRESSION,
                )
            )
            assert idle_scene_sync_count == scene_sync_count
            assert interval_activity == [], interval_activity
            assert idle_interval_activity == [], idle_interval_activity

        # Re-execution clears the cell before writing a new fragmented stream.
        # The second run must replace the first output rather than replaying any
        # of its fragments.
        _click_dom_element(
            bidi,
            context,
            "[...document.querySelectorAll('[role=menuitem]')]"
            ".find(node => node.textContent.trim() === 'Run')",
        )
        _click_dom_element(
            bidi,
            context,
            "[...document.querySelectorAll('[role=menuitem]')]"
            ".find(node => node.textContent.trim() === 'Run All Cells')",
        )
        expected_stream = "stream-run-2\nstream-fragment-19\nstream-end\n"
        _wait_for_expression(
            bidi,
            context,
            "document.body.innerText.includes('stream-run-2') && "
            "document.body.innerText.includes('stream-fragment-19') && "
            f"{render_condition} && "
            "!document.body.innerText.includes('Loading widget') && "
            "!document.body.innerText.includes('model not found')",
            timeout=45,
            log_paths=diagnostic_logs,
        )
        stream_outputs = json.loads(
            bidi.evaluate(
                context,
                "JSON.stringify([...document.querySelectorAll("
                "'.jp-OutputArea-output[data-mime-type=\"application/vnd.jupyter.stdout\"]'"
                ")].map(node => node.textContent))",
            )
        )
        assert stream_outputs == [expected_stream]

        # A second JupyterLab client replays the populated notebook room. It
        # must receive the same single stream output without duplicating the
        # suffix while reconstructing its local output model.
        replay_context = bidi.request("browsingContext.create", {"type": "tab"})[
            "context"
        ]
        bidi.request(
            "browsingContext.navigate",
            {
                "context": replay_context,
                "url": (
                    f"http://127.0.0.1:{server_port}/lab/tree/widget.ipynb"
                    f"?token={token}"
                ),
                "wait": "complete",
            },
        )
        _wait_for_expression(
            bidi,
            replay_context,
            "document.body.innerText.includes('stream-run-2') && "
            "document.body.innerText.includes('stream-fragment-19') && "
            f"{render_condition} && "
            "!document.body.innerText.includes('Loading widget') && "
            "!document.body.innerText.includes('model not found')",
            timeout=45,
            log_paths=diagnostic_logs,
        )
        replay_stream_outputs = json.loads(
            bidi.evaluate(
                replay_context,
                "JSON.stringify([...document.querySelectorAll("
                "'.jp-OutputArea-output[data-mime-type=\"application/vnd.jupyter.stdout\"]'"
                ")].map(node => node.textContent))",
            )
        )
        assert replay_stream_outputs == [expected_stream]
        browser_errors = [
            event["params"]["text"]
            for event in bidi.events
            if event.get("method") == "log.entryAdded"
            and event["params"].get("level") == "error"
        ]
        stream_output_errors = [
            error
            for error in browser_errors
            if "appendStreamOutput" in error or "insert is not a function" in error
        ]
        assert not stream_output_errors, stream_output_errors

        # Re-execution destroyed nine earlier NiiVue models; without the
        # model-cleanup patch their WebGL contexts stay alive and the
        # browser starts evicting contexts.
        if include_niivue:
            context_exhaustion = [
                event["params"]["text"]
                for event in bidi.events
                if event.get("method") == "log.entryAdded"
                and "webgl context" in event["params"].get("text", "").lower()
                and any(
                    phrase in event["params"]["text"].lower()
                    for phrase in ("too many", "exceeded", "losing", "oldest")
                )
            ]
            assert not context_exhaustion, context_exhaustion
    finally:
        if browser is not None:
            browser.close()
        _stop(server)
        server_log.close()
