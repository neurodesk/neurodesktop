"""Tests for cvmfs_server_select.sh: throughput-ranked CVMFS server selection.

The script is exercised against local mock HTTP servers that serve a fake
CVMFS repository layout (.cvmfspublished manifest + root catalog object), so
these tests need no network access and no root privileges.
"""

import functools
import http.server
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest


REPO = "neurodesk.ardc.edu.au"
CATALOG_HASH = "ab" + "0123456789" * 3 + "abcdefabcd"  # 40 hex chars
CATALOG_BYTES = os.urandom(200 * 1024)


def _script_path():
    installed = "/opt/neurodesktop/cvmfs_server_select.sh"
    if os.path.isfile(installed):
        return installed
    return str(
        Path(__file__).resolve().parent.parent
        / "config"
        / "jupyter"
        / "cvmfs_server_select.sh"
    )


def _build_mock_repo(root: Path):
    repo_dir = root / "cvmfs" / REPO
    manifest = f"C{CATALOG_HASH}\nB1234\nRd41d8cd98f00b204e9800998ecf8427e\n"
    (repo_dir / "data" / CATALOG_HASH[:2]).mkdir(parents=True)
    (repo_dir / ".cvmfspublished").write_text(manifest)
    (repo_dir / "data" / CATALOG_HASH[:2] / (CATALOG_HASH[2:] + "C")).write_bytes(
        CATALOG_BYTES
    )


class _SlowHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the mock repo with an artificial delay on every request."""

    delay = 0.5

    def do_GET(self):
        time.sleep(self.delay)
        super().do_GET()

    def log_message(self, *args):
        pass


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def mock_repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("mock_cvmfs_repo")
    _build_mock_repo(root)
    return root


def _start_server(root: Path, handler_cls):
    handler = functools.partial(handler_cls, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture(scope="module")
def fast_server(mock_repo):
    server, base = _start_server(mock_repo, _QuietHandler)
    yield base
    server.shutdown()


@pytest.fixture(scope="module")
def slow_server(mock_repo):
    server, base = _start_server(mock_repo, _SlowHandler)
    yield base
    server.shutdown()


@pytest.fixture()
def dead_server_url():
    """A URL that refuses connections (bound but never accepting)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens on this port any more
    return f"http://127.0.0.1:{port}"


def run_select(tmp_path, host_pool, extra_env=None, args=()):
    env = os.environ.copy()
    env.update(
        {
            "NEURODESKTOP_CVMFS_HOST_POOL": host_pool,
            "NEURODESKTOP_CVMFS_TARGET_CONFIG": str(tmp_path / "repo.conf"),
            "NEURODESKTOP_CVMFS_CACHE_FILE": str(tmp_path / "selection.env"),
        }
    )
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        ["bash", _script_path(), *args],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    )
    config = tmp_path / "repo.conf"
    return proc, (config.read_text() if config.is_file() else "")


def _configured_server_urls(config):
    server_line = next(
        line for line in config.splitlines() if line.startswith("CVMFS_SERVER_URL=")
    )
    return server_line.split('"')[1].split(";")


def test_script_syntax_ok():
    code = subprocess.run(["bash", "-n", _script_path()]).returncode
    assert code == 0, "cvmfs_server_select.sh has a bash syntax error"


def test_ranked_config_written(tmp_path, fast_server):
    proc, config = run_select(tmp_path, fast_server)

    assert proc.returncode == 0, proc.stdout
    assert 'CVMFS_USE_GEOAPI=no' in config
    assert f"{fast_server}/cvmfs/@fqrn@" in config
    assert 'CVMFS_KEYS_DIR="/etc/cvmfs/keys/ardc.edu.au/"' in config

    cache = (tmp_path / "selection.env").read_text()
    assert "CACHED_CVMFS_SERVER_URL=" in cache
    assert "CACHED_TIMESTAMP=" in cache


def test_faster_server_ranked_first(tmp_path, fast_server, slow_server):
    proc, config = run_select(tmp_path, f"{slow_server} {fast_server}")

    assert proc.returncode == 0, proc.stdout
    server_line = next(
        line for line in config.splitlines() if line.startswith("CVMFS_SERVER_URL=")
    )
    servers = _configured_server_urls(config)
    assert servers[0] == f"{fast_server}/cvmfs/@fqrn@", (
        f"Expected the fast server first, got: {server_line}\n{proc.stdout}"
    )
    # The slow server is still listed as a fallback.
    assert f"{slow_server}/cvmfs/@fqrn@" in servers


def test_unreachable_host_excluded(tmp_path, fast_server, dead_server_url):
    proc, config = run_select(tmp_path, f"{dead_server_url} {fast_server}")

    assert proc.returncode == 0, proc.stdout
    assert f"{fast_server}/cvmfs/@fqrn@" in config
    assert dead_server_url not in config


def test_all_unreachable_writes_fallback(tmp_path, dead_server_url):
    proc, config = run_select(tmp_path, dead_server_url)

    assert proc.returncode == 1, proc.stdout
    assert "CVMFS_USE_GEOAPI=yes" in config
    server_hosts = {urlparse(url).hostname for url in _configured_server_urls(config)}
    assert "cvmfs-geoproximity.neurodesk.org" in server_hosts
    # A failed probe must not poison the cache.
    assert not (tmp_path / "selection.env").is_file()


def test_cached_selection_reused(tmp_path, fast_server):
    proc1, config1 = run_select(tmp_path, fast_server)
    assert proc1.returncode == 0, proc1.stdout

    proc2, config2 = run_select(tmp_path, fast_server)
    assert proc2.returncode == 0, proc2.stdout
    assert "Using cached server selection" in proc2.stdout
    assert "Stage 1" not in proc2.stdout
    assert config2 == config1


def test_expired_cache_triggers_reprobe(tmp_path, fast_server):
    proc1, _ = run_select(tmp_path, fast_server)
    assert proc1.returncode == 0, proc1.stdout

    proc2, _ = run_select(
        tmp_path,
        fast_server,
        extra_env={"NEURODESKTOP_CVMFS_SELECTION_TTL_SECONDS": "0"},
    )
    assert proc2.returncode == 0, proc2.stdout
    assert "Stage 1" in proc2.stdout


def test_force_probe_ignores_cache(tmp_path, fast_server):
    proc1, _ = run_select(tmp_path, fast_server)
    assert proc1.returncode == 0, proc1.stdout

    proc2, _ = run_select(tmp_path, fast_server, args=("--force-probe",))
    assert proc2.returncode == 0, proc2.stdout
    assert "Stage 1" in proc2.stdout


def test_probes_are_cache_busted(tmp_path, mock_repo):
    """Every manifest and catalog probe must carry a unique cache-busting
    query string so CDN edge caches cannot inflate the measured speed —
    most users hit repository objects cold."""

    class _RecordingHandler(_QuietHandler):
        requests = []

        def do_GET(self):
            _RecordingHandler.requests.append(self.path)
            super().do_GET()

    server, base = _start_server(mock_repo, _RecordingHandler)
    try:
        proc, _ = run_select(tmp_path, base)
        assert proc.returncode == 0, proc.stdout
    finally:
        server.shutdown()

    requests = _RecordingHandler.requests
    catalog_probes = [p for p in requests if "/data/" in p]
    manifest_probes = [p for p in requests if ".cvmfspublished" in p]

    assert len(catalog_probes) >= 2, requests
    assert len(manifest_probes) >= 2, requests
    bare = [p for p in catalog_probes + manifest_probes if "cvmfsselect=" not in p]
    assert not bare, f"Probes without cache-busting query: {bare}"
    # No two probes may reuse the same query value, or the second fetch
    # would hit the cache the first one warmed.
    queries = [p.split("cvmfsselect=")[1] for p in requests if "cvmfsselect=" in p]
    assert len(queries) == len(set(queries)), f"Reused cache-bust values: {queries}"


def test_unhealthy_cached_primary_triggers_reprobe(tmp_path, mock_repo, fast_server):
    # First run against a server that then goes away.
    doomed, doomed_base = _start_server(mock_repo, _QuietHandler)
    proc1, _ = run_select(tmp_path, doomed_base)
    assert proc1.returncode == 0, proc1.stdout
    doomed.shutdown()
    doomed.server_close()

    # Cache points at the dead server; the health check must reject it and
    # a fresh probe must pick the live one.
    proc2, config2 = run_select(tmp_path, fast_server)
    assert proc2.returncode == 0, proc2.stdout
    assert "Stage 1" in proc2.stdout
    assert f"{fast_server}/cvmfs/@fqrn@" in config2
