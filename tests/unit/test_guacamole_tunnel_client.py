import importlib.util
import sys

import pytest

from testlib import repo_path


@pytest.fixture
def desktop_tests():
    container_tests = repo_path("tests/container")
    sys.path.insert(0, str(container_tests))
    try:
        spec = importlib.util.spec_from_file_location(
            "guacamole_desktop_tests",
            container_tests / "test_desktops.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(container_tests))


def test_desktop_frame_collector_acknowledges_guacamole_sync(desktop_tests):
    """Model guacd timing out a tunnel client that never acknowledges sync."""

    class SyncGatedTunnel:
        def __init__(self):
            self.sent = []
            self.receives = 0

        def recv(self):
            self.receives += 1
            if self.receives == 1:
                return "4.sync,13.1793682722893;"
            if self.receives == 2:
                assert self.sent == ["4.sync,13.1793682722893;"]
                return "3.img,1.0;4.sync,3.123;"
            raise desktop_tests.websocket.WebSocketTimeoutException()

        def send(self, frame):
            self.sent.append(frame)

    tunnel = SyncGatedTunnel()

    frames = desktop_tests._collect_guacamole_desktop_frames(tunnel)

    assert frames == [
        "4.sync,13.1793682722893;",
        "3.img,1.0;4.sync,3.123;",
    ]
    assert tunnel.sent == [
        "4.sync,13.1793682722893;",
        "4.sync,3.123;",
    ]
