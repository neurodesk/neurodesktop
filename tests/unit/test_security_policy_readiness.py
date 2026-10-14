import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def security_probe(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "container/test_security_policy.py"
    spec = importlib.util.spec_from_file_location("security_policy_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = SimpleNamespace(
        elapsed=0, responses=[b"HTTP/1.1 404 Not Found", b"HTTP/1.1 200 OK"],
        requests=0, exit_after=None, terminated=False, waited=False,
        fragment_size=None, close_after_fragment=False, connect_delay_fraction=0,
    )

    def sleep(seconds):
        state.elapsed += seconds
        assert state.elapsed <= 46, "Readiness exceeded its startup deadline"

    def start(command, stdout, stderr):
        endpoint = Path(command[command.index("--socket") + 1])
        endpoint.touch(mode=0o600)
        stdout.write("test server startup log\n")
        stdout.flush()
        return SimpleNamespace(
            poll=lambda: 7 if state.exit_after is not None and state.requests >= state.exit_after else None,
            terminate=lambda: setattr(state, "terminated", True),
            wait=lambda timeout=None: setattr(state, "waited", True),
        )

    class Client:
        def __init__(self, family):
            assert family == module.socket.AF_UNIX
            self.used = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, seconds):
            assert 0 < seconds <= min(10, 45 - state.elapsed)
            self.timeout = seconds

        def connect(self, endpoint):
            assert Path(endpoint).stat().st_mode & 0o777 == 0o600
            state.elapsed += self.timeout * state.connect_delay_fraction

        def sendall(self, request):
            assert request.startswith(b"GET /healthz HTTP/1.1\r\n")
            assert not self.used, "Each retry needs a fresh connection"
            self.used = True
            self.response = state.responses[min(state.requests, len(state.responses) - 1)]
            if not isinstance(self.response, TimeoutError):
                self.response += b"\r\n\r\n"
            state.requests += 1

        def recv(self, size):
            state.elapsed += min(10, self.timeout)
            if isinstance(self.response, TimeoutError):
                raise self.response
            size = min(size, state.fragment_size or size)
            response, self.response = self.response[:size], self.response[size:]
            if state.close_after_fragment:
                self.response = b""
            return response

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: state.elapsed, sleep=sleep))
    monkeypatch.setattr(module, "subprocess", SimpleNamespace(Popen=start))
    monkeypatch.setattr(module, "socket", SimpleNamespace(socket=Client, AF_UNIX=1))
    monkeypatch.setattr(module, "os", SimpleNamespace(geteuid=lambda: 1000))
    return module.test_code_server_private_socket_serves_owner_and_rejects_other_uid, state


def test_private_socket_waits_for_health_route(security_probe):
    probe, state = security_probe
    probe()
    assert state.requests == 2
    assert state.terminated and state.waited


def test_private_socket_reads_fragmented_status_line(security_probe):
    probe, state = security_probe
    state.responses = [b"HTTP/1.1 200 OK"]
    state.fragment_size = 10
    probe()
    assert state.requests == 1
    assert state.terminated and state.waited


def test_private_socket_rejects_success_after_startup_deadline(security_probe):
    probe, state = security_probe
    state.responses = [TimeoutError(), TimeoutError(), b"HTTP/1.1 200 OK"]
    state.connect_delay_fraction = 0.9
    with pytest.raises(pytest.fail.Exception) as failure:
        probe()
    assert "Health request timed out" in str(failure.value)
    assert "test server startup log" in str(failure.value)
    assert state.elapsed <= 45.1
    assert state.terminated and state.waited


def test_private_socket_rejects_unterminated_status_line(security_probe):
    probe, state = security_probe
    state.responses = [b"HTTP/1.1 200 OK"]
    state.fragment_size = 15
    state.close_after_fragment = True
    with pytest.raises(pytest.fail.Exception) as failure:
        probe()
    assert "test server startup log" in str(failure.value)
    assert state.elapsed <= 45.1
    assert state.terminated and state.waited


def test_private_socket_retries_timed_out_health_request(security_probe):
    probe, state = security_probe
    state.responses = [TimeoutError(), b"HTTP/1.1 200 OK"]
    probe()
    assert state.requests == 2
    assert state.terminated and state.waited


def test_private_socket_permanent_timeout_reports_status_and_log(security_probe):
    probe, state = security_probe
    state.responses = [TimeoutError()]
    with pytest.raises(pytest.fail.Exception) as failure:
        probe()
    assert "Health request timed out" in str(failure.value)
    assert "test server startup log" in str(failure.value)
    assert 45 <= state.elapsed <= 45.1
    assert state.terminated and state.waited


@pytest.mark.parametrize("response", [b"HTTP/1.1 404 Not Found", b"HTTP/1.1 503 Waiting for 200"])
def test_private_socket_never_ready_fails_with_status_and_log(security_probe, response):
    probe, state = security_probe
    state.responses = [response]
    with pytest.raises(pytest.fail.Exception) as failure:
        probe()
    assert response.decode() in str(failure.value)
    assert "test server startup log" in str(failure.value)
    assert 45 <= state.elapsed <= 45.1
    assert state.terminated and state.waited


@pytest.mark.parametrize("exit_after", [0, 1])
def test_private_socket_stops_waiting_when_server_exits(security_probe, exit_after):
    probe, state = security_probe
    state.exit_after = exit_after
    with pytest.raises(pytest.fail.Exception) as failure:
        probe()
    assert ("404" if exit_after else "No health response") in str(failure.value)
    assert "exit=7" in str(failure.value)
    assert "test server startup log" in str(failure.value)
    assert state.requests == exit_after
    assert state.elapsed < 45
    assert state.terminated and state.waited
