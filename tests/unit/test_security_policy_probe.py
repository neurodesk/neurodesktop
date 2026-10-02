"""Checkout contracts for the installed-image code-server socket probe."""

import socket
import threading
import time

from testlib import load_source_module


def load_security_policy_module():
    return load_source_module(
        "security_policy_image_test",
        "/opt/tests/test_security_policy.py",
        "tests/container/test_security_policy.py",
    )


class RunningProcess:
    def poll(self):
        return None


class ExitedProcess:
    def poll(self):
        return 1


def serve_statuses(endpoint, statuses):
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(endpoint))
    server.listen()
    requests = []

    def respond():
        with server:
            for status in statuses:
                connection, _ = server.accept()
                with connection:
                    requests.append(connection.recv(4096))
                    body = b"{}"
                    connection.sendall(
                        f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n"
                        "Connection: close\r\n\r\n".encode() + body
                    )

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    return thread, requests


def test_healthz_probe_retries_routes_registered_after_listen(tmp_path):
    module = load_security_policy_module()
    endpoint = tmp_path / "vscode.sock"
    thread, requests = serve_statuses(endpoint, ["404 Not Found", "200 OK"])

    status = module._wait_for_healthz(endpoint, RunningProcess(), time.monotonic() + 10)
    thread.join(timeout=5)

    assert status == b"HTTP/1.1 200 OK"
    assert len(requests) == 2
    assert all(request.startswith(b"GET /healthz HTTP/1.1\r\n") for request in requests)


def test_healthz_probe_reports_persistent_failure_at_deadline(tmp_path):
    module = load_security_policy_module()
    endpoint = tmp_path / "vscode.sock"
    serve_statuses(endpoint, ["404 Not Found"] * 100)

    status = module._wait_for_healthz(endpoint, RunningProcess(), time.monotonic() + 0.5)

    assert status == b"HTTP/1.1 404 Not Found"


def test_healthz_probe_stops_when_code_server_exits(tmp_path):
    module = load_security_policy_module()

    status = module._wait_for_healthz(tmp_path / "missing.sock", ExitedProcess(), time.monotonic() + 10)

    assert status == b""
