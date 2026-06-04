import importlib.util
from types import SimpleNamespace
from pathlib import Path


def _load_webapp_wrapper_module():
    repo_module_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "jupyter"
        / "webapp_wrapper"
        / "webapp_wrapper.py"
    )
    installed_module_path = (
        Path("/opt/neurodesktop") / "webapp_wrapper" / "webapp_wrapper.py"
    )
    module_path = (
        repo_module_path
        if repo_module_path.exists()
        else installed_module_path
    )
    spec = importlib.util.spec_from_file_location("webapp_wrapper", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DummyStatusHandler:
    def __init__(self):
        self.responses = []
        self.headers = []
        self.ended = False

    def send_response(self, code):
        self.responses.append(code)

    def send_header(self, name, value):
        self.headers.append((name, value))

    def end_headers(self):
        self.ended = True


def test_close_beacon_does_not_schedule_immediate_backend_stop(monkeypatch):
    wrapper = _load_webapp_wrapper_module()
    scheduled_close_checks = []
    direct_stops = []

    monkeypatch.setattr(wrapper, "log", lambda _message: None)
    monkeypatch.setattr(
        wrapper,
        "stop_backend_for_idle",
        lambda idle_for: direct_stops.append(idle_for),
    )
    monkeypatch.setattr(
        wrapper,
        "_schedule_close_check",
        lambda: scheduled_close_checks.append("scheduled"),
        raising=False,
    )

    handler = DummyStatusHandler()
    wrapper.WebappHandler._send_status(handler, "POST", is_close=True)

    assert handler.responses == [204]
    assert ("Cache-Control", "no-cache") in handler.headers
    assert handler.ended
    assert scheduled_close_checks == []
    assert direct_stops == []


def test_location_rewrites_keep_relative_redirects_under_app_path():
    wrapper = _load_webapp_wrapper_module()
    wrapper.config = SimpleNamespace(app_name="jamovi")
    handler = object.__new__(wrapper.WebappHandler)
    handler.path = "/user/alice/jamovi"

    assert (
        wrapper.WebappHandler._rewrite_location(handler, "session-id/", 42037)
        == "/user/alice/jamovi/session-id/"
    )
    assert (
        wrapper.WebappHandler._rewrite_location(handler, "/assets/main.js", 42037)
        == "/user/alice/jamovi/assets/main.js"
    )
    assert (
        wrapper.WebappHandler._rewrite_location(
            handler,
            "http://localhost:42037/session-id/",
            42037,
        )
        == "/user/alice/jamovi/session-id/"
    )
    assert (
        wrapper.WebappHandler._rewrite_location(
            handler,
            "https://example.org/external/",
            42037,
        )
        == "https://example.org/external/"
    )


def test_path_rewrite_map_supports_explicit_base_path_targets():
    wrapper = _load_webapp_wrapper_module()

    rewrite_map = wrapper.build_path_rewrite_map(
        [
            {"from": "/assets/", "to": "${base_path}assets/"},
            "/jamovi/",
        ],
        "/user/alice/jamovi/",
    )

    assert (b"/assets/", b"/user/alice/jamovi/assets/") in rewrite_map
    assert (b"/jamovi/", b"/user/alice/jamovi/") in rewrite_map

    html = wrapper.apply_path_rewrites(
        b'<script src="/assets/main.js"></script>',
        rewrite_map,
    )
    assert html == b'<script src="/user/alice/jamovi/assets/main.js"></script>'
