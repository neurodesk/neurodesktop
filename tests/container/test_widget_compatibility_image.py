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

WIDGET_RENDERER_DIAGNOSTICS_EXPRESSION = r"""(async () => {
    const panels = [...window.jupyterapp.shell.widgets()].filter(
        panel => panel.context?.path === 'widget.ipynb'
    );
    const renderers = [];
    for (const panel of panels) {
        const cells = panel.content?.widgets || [];
        for (const [cellIndex, cell] of cells.entries()) {
            const outputItems = cell.outputArea?.widgets || [];
            for (const [outputIndex, outputItem] of outputItems.entries()) {
                const children = typeof outputItem.children === 'function'
                    ? [...outputItem.children()]
                    : [outputItem];
                for (const renderer of children) {
                    if (!renderer.node?.textContent?.includes('Loading widget')) {
                        continue;
                    }
                    const manager = renderer._manager?.promise
                        ? await Promise.race([
                            renderer._manager.promise,
                            new Promise(resolve => setTimeout(
                                () => resolve(null), 100
                            )),
                        ])
                        : null;
                    renderers.push({
                        cellIndex,
                        outputIndex,
                        rendererClass: renderer.constructor?.name,
                        managerKeys: manager ? Object.keys(manager) : [],
                        managerStatus: manager ? 'resolved' : 'pending',
                        restoredStatus: manager?.restoredStatus ?? null,
                        kernelRestoreInProgress:
                            manager?._kernelRestoreInProgress ?? null,
                        controlRetryInProgress:
                            manager?.__neurodesktopControlRetry ?? null,
                        controlRetryCount:
                            manager?.__neurodesktopControlRetryCount ?? null,
                        kernelProbeStatus:
                            manager?.__neurodesktopKernelProbeStatus ?? null,
                        missingModelRecoveryInProgress: Boolean(
                            manager?.__neurodesktopMissingModelRecovery
                        ),
                        modelIds: manager?._modelsSync
                            ? [...manager._modelsSync.keys()]
                            : [],
                        kernelId: manager?.kernel?.id ?? null,
                        kernelStatus: manager?.kernel?.status ?? null,
                        kernelConnectionStatus:
                            manager?.kernel?.connectionStatus ?? null,
                        hasRerenderModel:
                            renderer._rerenderMimeModel !== null,
                    });
                }
            }
        }
    }
    return JSON.stringify({panelCount: panels.length, renderers});
})()"""

WIDGET_RENDERER_MANAGER_EXPRESSION = r"""(async () => {
    const panels = [...window.jupyterapp.shell.widgets()].filter(
        panel => panel.context?.path === 'widget.ipynb'
    );
    const renderers = [];
    for (const panel of panels) {
        for (const [cellIndex, cell] of panel.content.widgets.entries()) {
            for (const [outputIndex, outputItem] of
                    (cell.outputArea?.widgets || []).entries()) {
                const children = typeof outputItem.children === 'function'
                    ? [...outputItem.children()]
                    : [outputItem];
                for (const renderer of children) {
                    // WidgetRenderer owns this promise delegate even when its
                    // manager-backed factory has not resolved it.
                    if (!('_manager' in renderer)) {
                        continue;
                    }
                    let manager = null;
                    let managerError = null;
                    try {
                        manager = renderer._manager?.promise
                            ? await Promise.race([
                                renderer._manager.promise,
                                new Promise(resolve => setTimeout(
                                    () => resolve(null), 1000
                                )),
                            ])
                            : null;
                    } catch (error) {
                        managerError = String(error);
                    }
                    renderers.push({
                        cellIndex,
                        outputIndex,
                        rendererClass: renderer.constructor?.name ?? null,
                        managerResolved: Boolean(manager),
                        managerError,
                    });
                }
            }
        }
    }
    return JSON.stringify({panelCount: panels.length, renderers});
})()"""

WIDGET_RENDERER_OUTPUT_WATCH_EXPRESSION = r"""(async () => {
    const panel = [...window.jupyterapp.shell.widgets()].find(
        widget => widget.context?.path === 'widget.ipynb'
    );
    const cell = panel.content.widgets.find(
        candidate => candidate.outputArea?.widgets?.length
    );
    const outputArea = cell.outputArea;
    let outputItem = null;
    let renderer = null;
    for (const candidate of outputArea.widgets) {
        const children = typeof candidate.children === 'function'
            ? [...candidate.children()]
            : [candidate];
        const widgetRenderer = children.find(child => '_manager' in child);
        if (widgetRenderer) {
            outputItem = candidate;
            renderer = widgetRenderer;
            break;
        }
    }
    const manager = await renderer._manager.promise;
    const managerless = new renderer.constructor({
        mimeType: 'application/vnd.jupyter.widget-view+json',
    });
    const originalChildren = outputItem.children;
    const originalRenderers = [...outputItem.children()];
    outputItem.children = function* () {
        yield* originalRenderers;
        yield managerless;
    };
    try {
        outputArea.outputLengthChanged.emit(outputArea.widgets.length);
        const repairedManager = await Promise.race([
            managerless._manager.promise,
            new Promise(resolve => setTimeout(() => resolve(null), 1000)),
        ]);
        return JSON.stringify({
            repaired: repairedManager === manager,
        });
    } finally {
        outputItem.children = originalChildren;
        managerless.dispose();
    }
})()"""

WIDGET_RECOVERY_TRANSITIONS_EXPRESSION = r"""(async () => {
    const panel = [...window.jupyterapp.shell.widgets()].find(
        widget => widget.context?.path === 'widget.ipynb'
    );
    let sourceRenderer = null;
    let outputItem = null;
    for (const cell of panel.content.widgets) {
        for (const candidate of cell.outputArea?.widgets || []) {
            const children = typeof candidate.children === 'function'
                ? [...candidate.children()]
                : [candidate];
            sourceRenderer = children.find(child => child._manager?.promise);
            if (sourceRenderer) {
                outputItem = candidate;
                break;
            }
        }
        if (sourceRenderer) {
            break;
        }
    }
    const manager = await sourceRenderer._manager.promise;
    const modelId = 'delayed-hbox-model-1';
    const originalModel = await manager.get_model(modelId);
    const originalCreateComm = manager._create_comm;
    const originalGetCommInfo = manager._get_comm_info;
    const originalGetModel = manager.get_model;
    const originalRestoredStatus = manager._restoredStatus;
    const originalRecoveryAt =
        manager.__neurodesktopMissingModelRecoveryAt;
    const originalControlRetry = manager.__neurodesktopControlRetry;
    const originalControlRetryCount =
        manager.__neurodesktopControlRetryCount;
    const mimeModel = {data: {
        'application/vnd.jupyter.widget-view+json': {
            model_id: modelId,
            version_major: 2,
            version_minor: 0,
        },
    }};
    const recoveryRenderers = [0, 1].map(() => {
        const renderer = new sourceRenderer.constructor({
            mimeType: 'application/vnd.jupyter.widget-view+json',
        });
        renderer.manager = manager;
        outputItem.addWidget(renderer);
        return renderer;
    });
    const burstRenderer = new sourceRenderer.constructor({
        mimeType: 'application/vnd.jupyter.widget-view+json',
    });
    burstRenderer.manager = manager;
    outputItem.addWidget(burstRenderer);
    const lateRenderer = new sourceRenderer.constructor({
        mimeType: 'application/vnd.jupyter.widget-view+json',
    });
    lateRenderer.manager = manager;
    outputItem.addWidget(lateRenderer);
    let createCommFailures = 0;
    let commInfoFailures = 0;
    try {
        manager._restoredStatus = true;
        manager.__neurodesktopMissingModelRecoveryAt = 0;
        manager._create_comm = async () => {
            createCommFailures += 1;
            throw new Error('simulated control comm connection loss');
        };
        manager._get_comm_info = async () => {
            commInfoFailures += 1;
            throw new Error('simulated comm-info connection loss');
        };
        delete manager._models[modelId];
        manager._modelsSync?.delete(modelId);

        await Promise.all(
            recoveryRenderers.map(renderer => renderer.renderModel(mimeModel))
        );
        const failedTexts = recoveryRenderers.map(
            renderer => renderer.node.textContent
        );
        const retainedAfterFailure = recoveryRenderers.every(
            renderer => renderer._rerenderMimeModel === mimeModel
        );
        const recoveryCleared =
            manager.__neurodesktopMissingModelRecovery == null;

        manager._create_comm = originalCreateComm;
        manager._get_comm_info = originalGetCommInfo;
        manager.register_model(modelId, Promise.resolve(originalModel));
        await manager.restoreWidgets(panel.context.model, {
            loadKernel: false,
            loadNotebook: false,
        });
        const recoveryDeadline = Date.now() + 5000;
        while (Date.now() < recoveryDeadline && recoveryRenderers.some(
            renderer => renderer._rerenderMimeModel !== null ||
                renderer.widgets.length !== 1
        )) {
            await new Promise(resolve => setTimeout(resolve, 50));
        }

        // A restore signal is an edge notification, not a render count. The
        // renderer must consume its pending MIME model before its asynchronous
        // render starts so adjacent notifications cannot create duplicate views.
        burstRenderer.node.textContent =
            'Error displaying widget: model not found';
        burstRenderer._rerenderMimeModel = mimeModel;
        manager._restored.emit();
        manager._restored.emit();
        const burstDeadline = Date.now() + 5000;
        while (Date.now() < burstDeadline && burstRenderer.widgets.length < 1) {
            await new Promise(resolve => setTimeout(resolve, 50));
        }
        await new Promise(resolve => setTimeout(resolve, 250));

        // A delayed comm_open registers its model without running a bulk
        // restore. Registration must wake a renderer that already exhausted
        // recovery and retained its MIME model.
        manager.get_model = async () => {
            throw new Error('simulated model arriving after renderer failure');
        };
        await lateRenderer.renderModel(mimeModel);
        const lateFailedText = lateRenderer.node.textContent;
        manager.get_model = originalGetModel;
        delete manager._models[modelId];
        manager._modelsSync?.delete(modelId);
        manager.register_model(modelId, Promise.resolve(originalModel));
        const lateDeadline = Date.now() + 5000;
        while (Date.now() < lateDeadline && (
            lateRenderer._rerenderMimeModel !== null ||
            lateRenderer.widgets.length !== 1
        )) {
            await new Promise(resolve => setTimeout(resolve, 50));
        }

        return JSON.stringify({
            failedTexts,
            retainedAfterFailure,
            recoveryCleared,
            createCommFailures,
            commInfoFailures,
            recoveredViewCounts: recoveryRenderers.map(
                renderer => renderer.widgets.length
            ),
            recoveredTexts: recoveryRenderers.map(
                renderer => renderer.node.textContent
            ),
            rerenderCleared: recoveryRenderers.every(
                renderer => renderer._rerenderMimeModel === null
            ),
            burstViewCount: burstRenderer.widgets.length,
            burstRerenderCleared:
                burstRenderer._rerenderMimeModel === null,
            lateFailedText,
            lateArrivalViewCount: lateRenderer.widgets.length,
            lateArrivalText: lateRenderer.node.textContent,
            lateArrivalRerenderCleared:
                lateRenderer._rerenderMimeModel === null,
        });
    } finally {
        manager._create_comm = originalCreateComm;
        manager._get_comm_info = originalGetCommInfo;
        manager.get_model = originalGetModel;
        manager._restoredStatus = originalRestoredStatus;
        if (originalRecoveryAt === undefined) {
            delete manager.__neurodesktopMissingModelRecoveryAt;
        } else {
            manager.__neurodesktopMissingModelRecoveryAt = originalRecoveryAt;
        }
        if (originalControlRetry === undefined) {
            delete manager.__neurodesktopControlRetry;
        } else {
            manager.__neurodesktopControlRetry = originalControlRetry;
        }
        if (originalControlRetryCount === undefined) {
            delete manager.__neurodesktopControlRetryCount;
        } else {
            manager.__neurodesktopControlRetryCount = originalControlRetryCount;
        }
        for (const renderer of [
            ...recoveryRenderers,
            burstRenderer,
            lateRenderer,
        ]) {
            renderer.dispose();
        }
    }
})()"""

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
    widget_result = bidi.evaluate(
        context,
        WIDGET_RENDERER_DIAGNOSTICS_EXPRESSION,
    )
    try:
        widget_renderers = (
            json.loads(widget_result)
            if isinstance(widget_result, str)
            else widget_result
        )
    except json.JSONDecodeError:
        widget_renderers = widget_result
    return {
        "browserErrors": browser_errors,
        "webgl": _probe_webgl2(bidi, context),
        "state": state,
        "widgetRenderers": widget_renderers,
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


def _assert_widget_renderers_have_managers(
    bidi: _BidiSession,
    context: str,
    *,
    minimum: int,
) -> None:
    result = json.loads(
        bidi.evaluate(context, WIDGET_RENDERER_MANAGER_EXPRESSION)
    )
    assert result["panelCount"] == 1, result
    assert len(result["renderers"]) >= minimum, result
    assert all(
        renderer["managerResolved"] for renderer in result["renderers"]
    ), result


def _assert_output_watch_repairs_managerless_renderer(
    bidi: _BidiSession,
    context: str,
) -> None:
    result = json.loads(
        bidi.evaluate(context, WIDGET_RENDERER_OUTPUT_WATCH_EXPRESSION)
    )
    assert result == {"repaired": True}


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
        "    box = widgets.HBox([\n"
        "        widgets.IntSlider(value=42),\n"
        "        widgets.Label(value='nested'),\n"
        "    ], model_id=model_id)\n"
        "    if widget_run == 2:\n"
        "        for widget in (box, *box.children):\n"
        "            widget.comm.on_msg(lambda msg: None)\n"
        "    return box\n"
        "delayed_model_task = asyncio.create_task(open_model_later())\n"
        "if widget_run == 2:\n"
        "    original_control_handler = widgets.Widget._handle_control_comm_msg\n"
        "    control_request_count = 0\n"
        "    @classmethod\n"
        "    def delayed_control_handler(cls, msg, control_comm=None):\n"
        "        global control_request_count\n"
        "        control_request_count += 1\n"
        "        if control_request_count == 1:\n"
        "            return\n"
        "        def send_delayed_state():\n"
        "            original_control_handler(\n"
        "                msg, control_comm=control_comm)\n"
        "        asyncio.get_running_loop().call_later(\n"
        "            5,\n"
        "            send_delayed_state,\n"
        "        )\n"
        "    widgets.Widget._handle_control_comm_msg = delayed_control_handler\n"
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
    """Yjs output and bulk control state may arrive after frontend waits.

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
    assert "neurodesktop-widget-missing-model-recovery" in bundles
    assert "Date.now()-o<1e4" in bundles
    assert "__neurodesktopMissingModelRecovery" in bundles
    assert "__neurodesktopMissingModelRecoveryAt" in bundles
    assert "neurodesktop-widget-missing-model-restore-lifecycle" in bundles
    assert "Date.now()-neurodeskRecoveredAt<30e3" in bundles
    assert "neurodesktop-widget-control-timeout-staged-retry" in bundles
    assert "this.__neurodesktopControlRetry?3e4:1e4" in bundles
    assert "neurodesktop-widget-control-retry-reconnect" in bundles
    assert "neurodeskRetryKernel.reconnect()" in bundles
    assert "return await this._loadFromKernel()" in bundles
    assert "neurodeskRetries<2" in bundles
    assert "neurodesktop-widget-kernel-connection-reconnect" in bundles
    assert "if(!this.__neurodesktopControlRetry)" in bundles
    assert '"connected"!==neurodeskKernel.connectionStatus' in bundles
    assert "neurodeskKernel.requestKernelInfo()" in bundles
    assert "neurodeskKernel.reconnect().then(()=>!0)" in bundles
    assert "neurodesktop-widget-output-watch" in bundles
    assert "neurodesktop-widget-rerender-after-recovery-failure" in bundles
    assert "neurodesktop-widget-rerender-single-flight" in bundles
    assert "neurodesktop-widget-rerender-on-model-registration" in bundles


def test_widget_control_state_replies_return_to_requesting_client(monkeypatch):
    """Concurrent managers must not steal each other's state response."""
    from ipywidgets.widgets.widget import Widget

    class Comm:
        def __init__(self):
            self.callback = None
            self.sent = []

        def on_msg(self, callback):
            self.callback = callback

        def send(self, data, buffers=None):
            self.sent.append(data)

    first = Comm()
    second = Comm()
    opened = {"metadata": {"version": "1.0.0"}}
    request = {"content": {"data": {"method": "request_states"}}}

    monkeypatch.setattr(Widget, "_control_comm", None)
    Widget.handle_control_comm_opened(first, opened)
    Widget.handle_control_comm_opened(second, opened)
    first.callback(request)
    second.callback(request)

    assert [reply["method"] for reply in first.sent] == ["update_states"]
    assert [reply["method"] for reply in second.sent] == ["update_states"]


def test_server_documents_installs_reconnect_data_loss_guards() -> None:
    """Both halves of the blank-notebook reconnect fix are active assets."""
    import jupyter_server_documents

    package_dir = Path(jupyter_server_documents.__file__).parent
    yroom_source = (package_dir / "rooms/yroom.py").read_text(encoding="utf-8")
    assert "neurodesktop-late-sync-step2" in yroom_source
    assert "handshake_timeout = traitlets.Float(" in yroom_source
    assert "self._pending_ss2: dict[str, asyncio.Future[bytes]] = {}" in yroom_source
    assert "self._pending_ss2.pop(client_id, None)" in yroom_source

    # A fresh kernel WebSocket bridge must prove its ZMQ paths (the upstream
    # "nudge") before the listen tasks start; without it a new client's IOPub
    # subscription can silently miss the widget bulk-state reply.
    websocket_source = (package_dir / "websocket_connection.py").read_text(
        encoding="utf-8"
    )
    assert "neurodesktop-kernel-ws-nudge" in websocket_source
    assert "from . import _neurodesktop_kernel_nudge" in websocket_source
    assert "await _neurodesktop_kernel_nudge.nudge(self)" in websocket_source
    assert websocket_source.index("start_channels(hb=False)") < (
        websocket_source.index("await _neurodesktop_kernel_nudge.nudge(self)")
    ) < websocket_source.index("asyncio.create_task(self._listen(ch))")
    nudge_source = (package_dir / "_neurodesktop_kernel_nudge.py").read_text(
        encoding="utf-8"
    )
    assert "async def nudge" in nudge_source
    assert "forward_iopub_message" in nudge_source

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
    assert "neurodesktop-server-execution-dispatch-trust" in active_source
    assert "neurodesktop-server-execution-restore-trust" in active_source
    assert "neurodesktop-server-execution-trust" not in active_source
    trust_at = active_source.index("e.model.trusted=!0")
    assert active_source.index("l.hasNoKernel)return!0;") < trust_at
    assert active_source.index("c({cell:e});") < trust_at
    assert "try{e.model.trusted=!0;const n=await" in active_source
    assert active_source.count("e.model.trusted=neurodeskPrevTrusted") == 3
    subprocess.run(
        ["node", "--check", str(active_bundles[0])],
        check=True,
        capture_output=True,
        text=True,
    )


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
    assert "function createWidgetDefinition(){let vA,neurodeskDisposer;" in (
        shared_source
    )
    # One Disposer per model: upstream leaks render's own copy, whose
    # child-model listeners outlive the model and would later run
    # against the WebGL context this patch releases.
    assert "let I=neurodeskDisposer=new $B;" in shared_source
    assert "let B=neurodeskDisposer??(neurodeskDisposer=new $B);" in (
        shared_source
    )
    assert "vA=void 0,neurodeskDisposer=void 0" in shared_source
    assert "neurodesktop-ipyniivue-model-cleanup" in shared_source
    assert "neurodesktop-ipyniivue-event-scene-sync" in shared_source
    assert "setInterval(" not in shared_source
    assert 'getExtension("WEBGL_lose_context")?.loseContext()' in shared_source
    subprocess.run(
        ["node", "--check", str(shared_bundle)],
        check=True,
        capture_output=True,
        text=True,
    )


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
            "--LabApp.expose_app_in_browser=True",
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
        _assert_widget_renderers_have_managers(
            bidi,
            context,
            minimum=expected_widget_boxes,
        )
        _assert_output_watch_repairs_managerless_renderer(bidi, context)

        # Reproduce a comm_open that vanished before the browser received it.
        # Keep the model that represents the kernel-owned copy, remove only
        # the frontend registration, and inject it through the manager's bulk
        # recovery seam. A bounded wait alone makes the loss permanent.
        missing_model_recovery = json.loads(
            bidi.evaluate(
                context,
                "(async () => {"
                "const panel = [...window.jupyterapp.shell.widgets()].find("
                "widget => widget.context?.path === 'widget.ipynb');"
                "let manager = null;"
                "for (const cell of panel.content.widgets) {"
                "for (const outputItem of cell.outputArea?.widgets || []) {"
                "const children = typeof outputItem.children === 'function'"
                " ? [...outputItem.children()] : [outputItem];"
                "for (const renderer of children) {"
                "if (renderer._manager?.promise) {"
                "manager = await renderer._manager.promise; break;"
                "}"
                "}"
                "if (manager) break;"
                "}"
                "if (manager) break;"
                "}"
                "const modelId = 'delayed-hbox-model-1';"
                "const original = await manager.get_model(modelId);"
                "const originalLoad = manager._loadFromKernel.bind(manager);"
                "const originalRestoredStatus = manager._restoredStatus;"
                "const originalRecoveryAt = "
                "manager.__neurodesktopMissingModelRecoveryAt;"
                "let loadCalls = 0;"
                "let recoveryCalls = 0;"
                "const recoveryCallTimes = [];"
                "const restoreInProgress = [];"
                "let restoredSignals = 0;"
                "const onRestored = () => {restoredSignals += 1;};"
                "manager._restored.connect(onRestored);"
                "manager._restoredStatus = true;"
                "manager.__neurodesktopMissingModelRecoveryAt = 0;"
                "manager._loadFromKernel = async () => {"
                "loadCalls += 1;"
                "restoreInProgress.push(manager._kernelRestoreInProgress);"
                "await new Promise(resolve => setTimeout(resolve, 100));"
                "if (manager.__neurodesktopMissingModelRecovery) {"
                "recoveryCalls += 1;"
                "recoveryCallTimes.push(Date.now());"
                "manager.register_model(modelId, Promise.resolve(original));"
                "}"
                "};"
                "delete manager._models[modelId];"
                "manager._modelsSync?.delete(modelId);"
                "const started = Date.now();"
                "try {"
                "const [recovered, recoveredAgain] = await Promise.all(["
                "manager.get_model(modelId), manager.get_model(modelId)"
                "]);"
                "const elapsedMs = Date.now() - started;"
                "const recoveryCompletedAt = "
                "manager.__neurodesktopMissingModelRecoveryAt;"
                "const sequentialStarted = Date.now();"
                "let sequentialError = null;"
                "try {"
                "await manager.get_model('absent-sequential-model');"
                "} catch (error) {"
                "sequentialError = String(error);"
                "}"
                "return JSON.stringify({"
                "originalId: original.model_id,"
                "recoveredId: recovered.model_id,"
                "recoveredAgainId: recoveredAgain.model_id,"
                "hasModel: manager.has_model(modelId),"
                "recoveryCleared:"
                " manager.__neurodesktopMissingModelRecovery == null,"
                "loadCalls,"
                "recoveryCalls,"
                "recoveryCallTimes,"
                "restoreInProgress,"
                "restoredSignals,"
                "recoveryCompletedAt,"
                "recoveryAgeMs: Date.now() - recoveryCompletedAt,"
                "elapsedMs,"
                "sequentialElapsedMs: Date.now() - sequentialStarted,"
                "sequentialError,"
                "error: null"
                "});"
                "} catch (error) {"
                "return JSON.stringify({"
                "error: String(error),"
                "restoredStatus: manager.restoredStatus,"
                "hasModel: manager.has_model(modelId),"
                "modelIds: Object.keys(manager._models),"
                "kernelStatus: manager.kernel?.status,"
                "kernelConnectionStatus: manager.kernel?.connectionStatus,"
                "kernelProbeStatus:"
                " manager.__neurodesktopKernelProbeStatus ?? null,"
                "controlRetryCount:"
                " manager.__neurodesktopControlRetryCount ?? null,"
                "recoveryInProgress: Boolean("
                "manager.__neurodesktopMissingModelRecovery)"
                "});"
                "} finally {"
                "manager._restored.disconnect(onRestored);"
                "manager._loadFromKernel = originalLoad;"
                "manager._restoredStatus = originalRestoredStatus;"
                "if (originalRecoveryAt === undefined) {"
                "delete manager.__neurodesktopMissingModelRecoveryAt;"
                "} else {"
                "manager.__neurodesktopMissingModelRecoveryAt = "
                "originalRecoveryAt;"
                "}"
                "}"
                "})()",
            )
        )
        assert missing_model_recovery["error"] is None, missing_model_recovery
        assert missing_model_recovery["originalId"] == "delayed-hbox-model-1"
        assert missing_model_recovery["recoveredId"] == "delayed-hbox-model-1"
        assert missing_model_recovery["recoveredAgainId"] == "delayed-hbox-model-1"
        assert missing_model_recovery["hasModel"] is True
        assert missing_model_recovery["recoveryCleared"] is True
        assert missing_model_recovery["recoveryCalls"] == 1, (
            missing_model_recovery["recoveryCallTimes"],
            missing_model_recovery["recoveryCompletedAt"],
            missing_model_recovery["recoveryAgeMs"],
            missing_model_recovery["elapsedMs"],
            missing_model_recovery["sequentialElapsedMs"],
        )
        assert missing_model_recovery["restoreInProgress"]
        assert all(missing_model_recovery["restoreInProgress"])
        assert missing_model_recovery["restoredSignals"] >= 1
        assert missing_model_recovery["recoveryCompletedAt"] > 0
        assert 0 <= missing_model_recovery["recoveryAgeMs"] < 30_000
        assert missing_model_recovery["elapsedMs"] < 20_000
        assert "widget model not found" in (
            missing_model_recovery["sequentialError"]
        )
        assert missing_model_recovery["sequentialElapsedMs"] < 20_000

        # Drive the real get_model(), _loadFromKernel(), retry, fallback, and
        # restore lifecycle. Failure is injected below those methods at the
        # control-comm boundary so the renderer's visible failure and later
        # recovery cannot be bypassed by a successful high-level stub.
        recovery_transitions = json.loads(
            bidi.evaluate(
                context,
                WIDGET_RECOVERY_TRANSITIONS_EXPRESSION,
            )
        )
        assert recovery_transitions == {
            "failedTexts": [
                "Error displaying widget: model not found",
                "Error displaying widget: model not found",
            ],
            "retainedAfterFailure": True,
            "recoveryCleared": True,
            "createCommFailures": 3,
            "commInfoFailures": 1,
            "recoveredViewCounts": [1, 1],
            "recoveredTexts": ["42nested", "42nested"],
            "rerenderCleared": True,
            "burstViewCount": 1,
            "burstRerenderCleared": True,
            "lateFailedText": "Error displaying widget: model not found",
            "lateArrivalViewCount": 1,
            "lateArrivalText": "42nested",
            "lateArrivalRerenderCleared": True,
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
        _assert_widget_renderers_have_managers(
            bidi,
            context,
            minimum=expected_widget_boxes,
        )

        # A second JupyterLab client replays the populated notebook room. The
        # notebook drops the first bulk request, delays the retry beyond the
        # upstream four-second limit, and makes the racy per-model fallback
        # unavailable. The manager must retry and reproduce the stream.
        replay_context = bidi.request("browsingContext.create", {"type": "tab"})[
            "context"
        ]
        bidi.request(
            "browsingContext.navigate",
            {
                "context": replay_context,
                "url": (
                    f"http://127.0.0.1:{server_port}"
                    "/lab/workspaces/widget-replay"
                    f"?token={token}"
                ),
                "wait": "complete",
            },
        )
        replay_path = bidi.evaluate(replay_context, "location.pathname")
        assert replay_path == "/lab/workspaces/widget-replay"
        _wait_for_expression(
            bidi,
            replay_context,
            "Boolean(window.jupyterapp)",
            log_paths=diagnostic_logs,
        )
        assert bidi.evaluate(
            replay_context,
            "window.jupyterapp.restored"
            ".then(() => window.jupyterapp.allPluginsActivated)"
            ".then(() => true)",
        )
        assert bidi.evaluate(
            replay_context,
            "window.jupyterapp.commands.execute("
            "'docmanager:open', {path: 'widget.ipynb'}"
            ").then(() => true)",
        )
        _wait_for_expression(
            bidi,
            replay_context,
            "document.body.innerText.includes("
            "'Python [conda env:base] * | Idle'"
            ")",
            timeout=60,
            log_paths=diagnostic_logs,
        )
        _wait_for_expression(
            bidi,
            replay_context,
            "document.body.innerText.includes('stream-run-2') && "
            "document.body.innerText.includes('stream-fragment-19') && "
            f"{render_condition} && "
            "!document.body.innerText.includes('Loading widget') && "
            "!document.body.innerText.includes('model not found')",
            timeout=60,
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
        _assert_widget_renderers_have_managers(
            bidi,
            replay_context,
            minimum=expected_widget_boxes,
        )
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
        plugin_fetch_errors = [
            error
            for error in browser_errors
            if "NetworkError when attempting to fetch resource" in error
        ]
        assert not plugin_fetch_errors, plugin_fetch_errors

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
