"""Drive the desktop collector with delayed pixels and subsequent failures."""
import importlib.util
from types import SimpleNamespace

import pytest

from testlib import repo_path


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.syspath_prepend(str(repo_path("tests/container")))
    spec = importlib.util.spec_from_file_location(
        "desktop_observation_subject", repo_path("tests/container/test_desktops.py")
    )
    desktop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(desktop)
    return desktop


@pytest.mark.parametrize("late_error", [False, True])
def test_late_pixels_receive_full_observation(monkeypatch, desktop, late_error):
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


@pytest.mark.parametrize("frame_count", [False, True])
def test_collector_sends_sync_replies_and_idle_keepalives(monkeypatch, desktop, frame_count):
    now = [0]
    monkeypatch.setattr(desktop, "time", SimpleNamespace(
        time=lambda: now[0], monotonic=lambda: now[0],
    ))
    syncs = [
        "4.sync,3.123" + (",1.0" if frame_count else "") + ";",
        "4.sync,3.124" + (",1.1" if frame_count else "") + ";",
    ]
    sent = []

    class Tunnel:
        def send(self, frame):
            sent.append((now[0], frame))

        def recv(self):
            now[0] += 1
            if now[0] == 1:
                return "".join(syncs)
            if now[0] == 2:
                return "3.img;"
            raise desktop.websocket.WebSocketTimeoutException()

    desktop._collect_guacamole_desktop_frames(Tunnel(), 30, 6)

    replies = [frame for _, frame in sent if frame != "3.nop;"]
    decoded = desktop.GuacamoleInstructions().feed("".join(replies))
    assert [instruction[:2] for instruction in decoded] == [
        ["sync", "123"], ["sync", "124"],
    ]
    assert [at for at, frame in sent if frame == "3.nop;"] == [0, 2, 4, 6]
    assert now[0] == 8
