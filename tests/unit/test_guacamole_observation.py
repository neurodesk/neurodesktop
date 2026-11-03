"""Drive the desktop collector with delayed pixels and subsequent failures."""
import importlib.util
from types import SimpleNamespace

import pytest

from testlib import repo_path


@pytest.mark.parametrize("late_error", [False, True])
def test_late_pixels_receive_full_observation(monkeypatch, late_error):
    monkeypatch.syspath_prepend(str(repo_path("tests/container")))
    spec = importlib.util.spec_from_file_location(
        "desktop_observation_subject", repo_path("tests/container/test_desktops.py")
    )
    desktop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(desktop)
    now = [0]
    monkeypatch.setattr(desktop, "time", SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: now[0],
    ))

    class Tunnel:
        def send(self, frame):
            pass

        def recv(self):
            if now[0] == 0:
                now[0] = 29
                return "3.img;"
            now[0] += 1
            if late_error and now[0] == 35:
                return "5.error,4.late;"
            raise desktop.websocket.WebSocketTimeoutException()

    if late_error:
        with pytest.raises(AssertionError, match="error frame"):
            desktop._collect_guacamole_desktop_frames(Tunnel(), 30, 20)
    else:
        desktop._collect_guacamole_desktop_frames(Tunnel(), 30, 20)
        assert now[0] == 49
