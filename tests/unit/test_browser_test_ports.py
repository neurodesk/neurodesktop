"""Checkout contracts for browser-test port allocation."""

from testlib import load_source_module


def test_unused_ports_are_reserved_as_one_distinct_batch(monkeypatch):
    module = load_source_module(
        "rise_browser_test_ports",
        "/opt/tests/test_rise_slides_image.py",
        "tests/container/test_rise_slides_image.py",
    )
    active_ports = set()
    closed_ports = []

    class FakeSocket:
        def __init__(self):
            self.port = None

        def bind(self, address):
            assert address == ("127.0.0.1", 0)
            self.port = next(
                port for port in range(41000, 41003) if port not in active_ports
            )
            active_ports.add(self.port)

        def getsockname(self):
            return ("127.0.0.1", self.port)

        def close(self):
            active_ports.remove(self.port)
            closed_ports.append(self.port)

    monkeypatch.setattr(module.socket, "socket", FakeSocket)

    # Independent reservations can all receive the same port after each socket
    # is closed, which makes one test process occupy another one's endpoint.
    assert [module._unused_port() for _ in range(3)] == [41000, 41000, 41000]
    assert module._unused_ports(3) == (41000, 41001, 41002)
    assert active_ports == set()
    assert closed_ports == [41000, 41000, 41000, 41000, 41001, 41002]
