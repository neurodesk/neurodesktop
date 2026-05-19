import importlib.util
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
