"""Checkout contracts for the widget image test's Firefox harness."""

import ast
import inspect
import json

from testlib import load_source_module


def load_widget_test_module():
    return load_source_module(
        "widget_compatibility_image_test",
        "/opt/tests/test_widget_compatibility_image.py",
        "tests/container/test_widget_compatibility_image.py",
    )


def test_firefox_profile_allows_software_webgl_fallback(tmp_path, monkeypatch):
    module = load_widget_test_module()
    monkeypatch.setenv("HOME", "/unrelated-home")
    monkeypatch.setenv("LIBGL_ALWAYS_SOFTWARE", "true")

    home = tmp_path / "home"
    profile = tmp_path / "profile"
    home.mkdir()
    profile.mkdir()

    environment = module._firefox_environment(home)
    module._write_firefox_profile(profile)

    assert environment["HOME"] == str(home)
    assert "LIBGL_ALWAYS_SOFTWARE" not in environment
    assert (profile / "user.js").read_text(encoding="utf-8") == (
        'user_pref("webgl.disabled", false);\n'
        'user_pref("webgl.enable-webgl2", true);\n'
        'user_pref("webgl.forbid-software", false);\n'
        'user_pref("webgl.force-enabled", true);\n'
    )


def test_webgl_probe_reports_renderer_and_context_state():
    module = load_widget_test_module()
    expression = module.WEBGL2_DIAGNOSTICS_EXPRESSION

    assert "getContext('webgl2')" in expression
    assert "WEBGL_debug_renderer_info" in expression
    assert "UNMASKED_RENDERER_WEBGL" in expression
    assert "isContextLost" in expression
    assert "WEBGL_lose_context" in expression
    assert "navigator.userAgent" in expression


def test_widget_notebook_keeps_core_replay_coverage_without_webgl():
    module = load_widget_test_module()

    core_source = module._widget_regression_notebook(
        include_niivue=False
    ).cells[0].source
    webgl_source = module._widget_regression_notebook(
        include_niivue=True
    ).cells[0].source

    for source in (core_source, webgl_source):
        compile(
            source,
            "<widget-regression-cell>",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )
        assert "stream-fragment" in source
        assert "open_model_later" in source
        assert "widgets.HBox" in source
        assert "delayed-hbox-model" in source
        assert "asyncio.get_running_loop().call_later(" in source
        assert "            5," in source
        assert "widgets.Widget._handle_control_comm_msg" in source
        assert "widget.comm.on_msg(lambda msg: None)" in source
    assert "NiiVue" not in core_source
    assert "volume.nii.gz" not in core_source
    assert "NiiVue" in webgl_source
    assert "volume.nii.gz" in webgl_source
    assert "scene-sync-count:0" in webgl_source
    assert "niivue.observe(record_scene_sync, names='scene')" in webgl_source


def test_replay_client_uses_a_distinct_explicit_workspace():
    module = load_widget_test_module()
    source = inspect.getsource(
        module.test_server_side_execution_renders_streams_and_widgets
    )

    assert '"/lab/workspaces/widget-replay"' in source
    assert '"--LabApp.expose_app_in_browser=True"' in source
    assert "window.jupyterapp.allPluginsActivated" in source
    assert "'docmanager:open', {path: 'widget.ipynb'}" in source
    assert "NetworkError when attempting to fetch resource" in source


def test_ipyniivue_idle_probe_records_only_shared_asset_intervals():
    module = load_widget_test_module()

    preload = module.IPYNIIVUE_INTERVAL_PROBE_PRELOAD
    snapshot = module.IPYNIIVUE_INTERVAL_SNAPSHOT_EXPRESSION

    assert "nativeSetInterval = window.setInterval" in preload
    assert "stack.includes('neurodesktop-ipyniivue')" in preload
    assert "record.calls += 1" in preload
    assert "__neurodesktopIpyniivueIntervalActivity" in snapshot
    assert "scene-sync-count:" in module.SCENE_SYNC_COUNT_EXPRESSION


def test_browser_failure_diagnostics_include_process_logs(tmp_path):
    module = load_widget_test_module()
    firefox_log = tmp_path / "firefox.log"
    server_log = tmp_path / "server.log"
    firefox_log.write_text("firefox-start\nwebgl failed\n", encoding="utf-8")
    server_log.write_text("server-start\nroom ready\n", encoding="utf-8")

    class Bidi:
        events = [
            {
                "method": "log.entryAdded",
                "params": {"level": "error", "text": "widget error"},
            }
        ]

        def evaluate(self, context, expression):
            assert context == "browser-context"
            if expression == module.WEBGL2_DIAGNOSTICS_EXPRESSION:
                return json.dumps(
                    {
                        "available": True,
                        "renderer": "llvmpipe",
                        "contextLost": False,
                    }
                )
            return json.dumps({"body": "Loading widget...", "outputs": []})

    diagnostics = module._browser_failure_diagnostics(
        Bidi(),
        "browser-context",
        log_paths=(firefox_log, server_log),
    )

    assert diagnostics["browserErrors"] == ["widget error"]
    assert diagnostics["webgl"]["renderer"] == "llvmpipe"
    assert diagnostics["state"]["body"] == "Loading widget..."
    assert diagnostics["processLogs"] == {
        str(firefox_log): "firefox-start\nwebgl failed\n",
        str(server_log): "server-start\nroom ready\n",
    }


def test_firefox_webgl_startup_retries_only_capability_failures(
    tmp_path, monkeypatch
):
    module = load_widget_test_module()
    probes = iter(
        [
            {"available": False, "renderer": None, "contextLost": None},
            {"available": True, "renderer": "llvmpipe", "contextLost": False},
        ]
    )
    processes = []
    bidi_sessions = []

    class Process:
        def __init__(self, command, **kwargs):
            self.command = command
            self.environment = kwargs["env"]
            self.returncode = None
            processes.append(self)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout):
            return self.returncode

    class Bidi:
        def __init__(self, url, process):
            self.url = url
            self.process = process
            self.closed = False
            bidi_sessions.append(self)

        def request(self, method, params):
            if method == "browsingContext.create":
                return {"context": f"context-{len(bidi_sessions)}"}
            return {}

        def close(self):
            self.closed = True

    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module, "_BidiSession", Bidi)
    monkeypatch.setattr(module, "_unused_port", lambda: 41000 + len(processes))
    monkeypatch.setattr(module, "_probe_webgl2", lambda bidi, context: next(probes))

    browser = module._start_firefox_with_webgl_probe(tmp_path, attempts=3)
    try:
        assert len(processes) == 2
        assert processes[0].returncode == 0
        assert bidi_sessions[0].closed
        assert browser.process is processes[1]
        assert browser.context == "context-2"
        assert browser.webgl["renderer"] == "llvmpipe"
        assert "LIBGL_ALWAYS_SOFTWARE" not in processes[1].environment
        assert processes[0].command[0] == "/usr/bin/firefox"
        assert processes[0].command[1:3] == ["--headless", "--profile"]
    finally:
        browser.close()


def test_firefox_startup_returns_a_browser_when_webgl_is_unavailable(
    tmp_path, monkeypatch
):
    module = load_widget_test_module()
    processes = []
    bidi_sessions = []

    class Process:
        def __init__(self, command, **kwargs):
            self.returncode = None
            processes.append(self)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout):
            return self.returncode

    class Bidi:
        def __init__(self, url, process):
            self.closed = False
            bidi_sessions.append(self)

        def request(self, method, params):
            if method == "browsingContext.create":
                return {"context": f"context-{len(bidi_sessions)}"}
            return {}

        def close(self):
            self.closed = True

    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module, "_BidiSession", Bidi)
    monkeypatch.setattr(module, "_unused_port", lambda: 42000 + len(processes))
    monkeypatch.setattr(
        module,
        "_probe_webgl2",
        lambda bidi, context: {
            "available": False,
            "renderer": None,
            "contextLost": None,
        },
    )

    browser = module._start_firefox_with_webgl_probe(tmp_path, attempts=3)
    try:
        assert len(processes) == 3
        assert [process.returncode for process in processes] == [0, 0, None]
        assert [session.closed for session in bidi_sessions] == [True, True, False]
        assert browser.process is processes[2]
        assert browser.context == "context-3"
        assert not browser.webgl["available"]
    finally:
        browser.close()
