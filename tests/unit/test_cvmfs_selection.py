"""Tests for cvmfs_server_select.sh: throughput-ranked CVMFS server selection.

The script is exercised against local mock HTTP servers that serve a fake
CVMFS repository layout with a compressed SQLite catalog and data chunks, so
these tests need no network access and no root privileges.
"""

import functools
import hashlib
import sqlite3
import zlib
import http.server
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from testlib import resolve_source


REPO = "neurodesk.ardc.edu.au"
CHUNKS = [zlib.compress(os.urandom(size)) for size in (256 * 1024, 512 * 1024)]


def _script_path():
    return str(
        resolve_source(
            "/opt/neurodesktop/cvmfs_server_select.sh",
            "config/jupyter/cvmfs_server_select.sh",
        )
    )


def _build_mock_repo(root: Path):
    repo_dir = root / "cvmfs" / REPO
    repo_dir.mkdir(parents=True)
    db_path = root / "catalog.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE chunks (hash BLOB, size INTEGER)")
        db.execute("CREATE TABLE nested_catalogs (path TEXT, sha1 TEXT, size INTEGER)")
        for chunk in CHUNKS:
            digest = hashlib.sha1(chunk).hexdigest()
            db.execute("INSERT INTO chunks VALUES (?, ?)",
                       (bytes.fromhex(digest), len(zlib.decompress(chunk))))
            target = repo_dir / "data" / digest[:2] / (digest[2:] + "P")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(chunk)
    catalog = zlib.compress(db_path.read_bytes())
    digest = hashlib.sha1(catalog).hexdigest()
    target = repo_dir / "data" / digest[:2] / (digest[2:] + "C")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(catalog)
    (repo_dir / ".cvmfspublished").write_text(f"C{digest}\nS42\n")



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


def test_root_cache_write_restores_notebook_home_ownership(tmp_path, fast_server):
    """Execute the ownership repair without needing root on the test host."""
    home = tmp_path / "home"
    home.mkdir()
    cache = home / ".cache" / "neurodesktop" / "selection.env"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "chown.log"
    for name, body in {
        "id": "echo 0",
        "stat": "echo 1234",
        "chown": 'test -f "$TEST_CACHE" || exit 1; printf "%s\\n" "$*" >> "$TEST_CHOWN_LOG"',
    }.items():
        shim = bin_dir / name
        shim.write_text("#!/bin/sh\n" + body + "\n")
        shim.chmod(0o755)
    proc, _ = run_select(tmp_path, fast_server, extra_env={
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "HOME": str(home), "NB_UID": "1234", "NB_GID": "4321",
        "NEURODESKTOP_CVMFS_CACHE_FILE": str(cache),
        "TEST_CACHE": str(cache), "TEST_CHOWN_LOG": str(calls),
    })
    assert proc.returncode == 0, proc.stdout
    assert calls.read_text().splitlines() == [
        f"1234:4321 {cache}", f"1234:4321 {cache.parent}",
        f"1234:4321 {home / '.cache'}",
    ]


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


def test_incomplete_http_200_never_beats_complete_server(tmp_path, mock_repo, slow_server):
    class Truncated(_QuietHandler):
        def do_GET(self):
            if "/data/" in self.path:
                self.send_response(200)
                self.send_header("Content-Length", "1000000")
                self.end_headers()
                self.wfile.write(b"incomplete")
                self.close_connection = True
            else:
                super().do_GET()

    server, base = _start_server(mock_repo, Truncated)
    try:
        proc, config = run_select(tmp_path, f"{base} {slow_server}")
        assert proc.returncode == 0, proc.stdout
        assert _configured_server_urls(config)[0].startswith(slow_server + "/")
        assert base not in config
    finally:
        server.shutdown()
        server.server_close()


def test_all_reachable_servers_get_throughput_test(tmp_path, mock_repo):
    servers = []
    seen = []
    try:
        for index in range(7):
            class Recording(_QuietHandler):
                number = index

                def do_GET(self):
                    if "/data/" in self.path:
                        seen.append(self.number)
                    super().do_GET()

            servers.append(_start_server(mock_repo, Recording))
        proc, _ = run_select(tmp_path, " ".join(base for _, base in servers))
        assert proc.returncode == 0, proc.stdout
        assert set(seen) == set(range(7)), proc.stdout
    finally:
        for server, _ in servers:
            server.shutdown()
            server.server_close()


def test_real_file_chunks_are_benchmarked(tmp_path, mock_repo):
    seen = []

    class Recording(_QuietHandler):
        def do_GET(self):
            seen.append(urlparse(self.path).path)
            super().do_GET()

    server, base = _start_server(mock_repo, Recording)
    try:
        proc, _ = run_select(tmp_path, base)
        assert proc.returncode == 0, proc.stdout
        assert len({path for path in seen if path.endswith("P")}) >= 2
    finally:
        server.shutdown()
        server.server_close()


def test_direct_aliases_do_not_occupy_multiple_slots(tmp_path, fast_server):
    alias = fast_server.replace("127.0.0.1", "localhost")
    proc, config = run_select(tmp_path, f"{fast_server} {alias}")
    assert proc.returncode == 0, proc.stdout
    assert len(_configured_server_urls(config)) == 1
    assert "duplicate destination" in proc.stdout


def test_chunk_performance_overrides_catalog_ranking(tmp_path, mock_repo):
    class FastCatalog(_QuietHandler):
        def do_GET(self):
            if urlparse(self.path).path.endswith("P"):
                time.sleep(0.2)
            super().do_GET()

    class FastChunks(_QuietHandler):
        def do_GET(self):
            if urlparse(self.path).path.endswith("C"):
                time.sleep(0.05)
            super().do_GET()

    slow, slow_base = _start_server(mock_repo, FastCatalog)
    fast, fast_base = _start_server(mock_repo, FastChunks)
    try:
        proc, config = run_select(tmp_path, f"{slow_base} {fast_base}")
        assert proc.returncode == 0, proc.stdout
        assert _configured_server_urls(config)[0].startswith(fast_base + "/")
    finally:
        for server in [slow, fast]:
            server.shutdown()
            server.server_close()


def test_corrupt_complete_body_excluded(tmp_path, mock_repo, fast_server):
    class Corrupt(_QuietHandler):
        def do_GET(self):
            if "/data/" in self.path:
                self.send_response(200)
                self.send_header("Content-Length", "7")
                self.end_headers()
                self.wfile.write(b"corrupt")
            else:
                super().do_GET()

    server, base = _start_server(mock_repo, Corrupt)
    try:
        proc, config = run_select(tmp_path, f"{base} {fast_server}")
        assert proc.returncode == 0, proc.stdout
        assert base not in config
    finally:
        server.shutdown()
        server.server_close()


def test_one_failed_chunk_disqualifies_fast_finalist(tmp_path, mock_repo, slow_server):
    class Intermittent(_QuietHandler):
        def do_GET(self):
            if hashlib.sha1(CHUNKS[1]).hexdigest()[2:] in self.path:
                self.send_error(503)
            else:
                super().do_GET()

    server, base = _start_server(mock_repo, Intermittent)
    try:
        proc, config = run_select(tmp_path, f"{base} {slow_server}")
        assert proc.returncode == 0, proc.stdout
        assert base not in config
        assert _configured_server_urls(config)[0].startswith(slow_server + "/")
    finally:
        server.shutdown()
        server.server_close()


def test_cached_primary_slowdown_triggers_reprobe(tmp_path, mock_repo):
    class Variable(_QuietHandler):
        delay = 0

        def do_GET(self):
            if "/data/" in self.path:
                time.sleep(self.delay)
            super().do_GET()

    server, base = _start_server(mock_repo, Variable)
    try:
        proc, _ = run_select(tmp_path, base)
        assert proc.returncode == 0, proc.stdout
        Variable.delay = 0.1
        proc, _ = run_select(tmp_path, base)
        assert proc.returncode == 0, proc.stdout
        assert "below half" in proc.stdout
        assert "Stage 1" in proc.stdout
    finally:
        server.shutdown()
        server.server_close()


def test_legacy_cache_is_data_not_executable_shell(tmp_path, fast_server):
    marker = tmp_path / "executed"
    (tmp_path / "selection.env").write_text(
        f"touch {marker}\nCACHED_TIMESTAMP={int(time.time())}\n"
        f'CACHED_CVMFS_SERVER_URL="{fast_server}/cvmfs/@fqrn@"\n'
    )
    proc, _ = run_select(tmp_path, fast_server)
    assert proc.returncode == 0, proc.stdout
    assert "Stage 1" in proc.stdout
    assert not marker.exists()


def test_all_corrupt_objects_fall_back_without_cache(tmp_path, mock_repo):
    class Broken(_QuietHandler):
        def do_GET(self):
            if "/data/" in self.path:
                self.send_error(503)
            else:
                super().do_GET()

    server, base = _start_server(mock_repo, Broken)
    try:
        proc, config = run_select(tmp_path, base)
        assert proc.returncode == 1
        assert "CVMFS_USE_GEOAPI=yes" in config
        assert not (tmp_path / "selection.env").exists()
    finally:
        server.shutdown()
        server.server_close()


def test_cdn_virtual_hosts_remain_distinct():
    from testlib import load_source_module

    selector = load_source_module(
        "cvmfs_server_select", "/opt/neurodesktop/cvmfs_server_select.py",
        "config/jupyter/cvmfs_server_select.py",
    )
    first = {"base": "http://s1fnal-cvmfs.openhtc.io:8080", "ip": "192.0.2.1"}
    second = {"base": "http://s1osggoc-cvmfs.openhtc.io:8080", "ip": "192.0.2.1"}
    assert selector.destination(first) != selector.destination(second)


def test_faster_cached_challenger_triggers_ranking(tmp_path, mock_repo):
    class Primary(_QuietHandler):
        def do_GET(self):
            if "/data/" in self.path:
                time.sleep(0.02)
            super().do_GET()

    class Challenger(_QuietHandler):
        delay = 0.1

        def do_GET(self):
            if "/data/" in self.path:
                time.sleep(self.delay)
            super().do_GET()

    first, first_base = _start_server(mock_repo, Primary)
    second, second_base = _start_server(mock_repo, Challenger)
    try:
        pool = f"{first_base} {second_base}"
        proc, config = run_select(tmp_path, pool)
        assert proc.returncode == 0, proc.stdout
        assert _configured_server_urls(config)[0].startswith(first_base + "/")
        Challenger.delay = 0
        proc, config = run_select(tmp_path, pool)
        assert proc.returncode == 0, proc.stdout
        assert "Cached fallback is over 20% faster" in proc.stdout
        assert _configured_server_urls(config)[0].startswith(second_base + "/")
    finally:
        for server in [first, second]:
            server.shutdown()
            server.server_close()


def test_one_day_default_expires_previous_days_ranking(tmp_path, fast_server):
    proc, _ = run_select(tmp_path, fast_server)
    assert proc.returncode == 0, proc.stdout
    cache = tmp_path / "selection.env"
    lines = cache.read_text().splitlines()
    cache.write_text("\n".join(
        f"CACHED_TIMESTAMP={int(time.time()) - 36 * 3600}" if line.startswith("CACHED_TIMESTAMP=")
        else line for line in lines
    ) + "\n")
    proc, _ = run_select(tmp_path, fast_server)
    assert proc.returncode == 0, proc.stdout
    assert "Stage 1" in proc.stdout


def test_catalog_only_repository_can_still_be_ranked(tmp_path):
    _build_mock_repo(tmp_path / "repo")
    db_path = tmp_path / "repo/catalog.db"
    with sqlite3.connect(db_path) as db:
        db.execute("DELETE FROM chunks")
    catalog = zlib.compress(db_path.read_bytes())
    digest = hashlib.sha1(catalog).hexdigest()
    repo = tmp_path / "repo/cvmfs" / REPO
    target = repo / "data" / digest[:2] / (digest[2:] + "C")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(catalog)
    (repo / ".cvmfspublished").write_text(f"C{digest}\nS43\n")
    server, base = _start_server(tmp_path / "repo", _QuietHandler)
    try:
        proc, config = run_select(tmp_path, base)
        assert proc.returncode == 0, proc.stdout
        assert "supplementing" in proc.stdout
        assert _configured_server_urls(config)[0].startswith(base + "/")
    finally:
        server.shutdown()
        server.server_close()


def test_failed_finalists_are_replaced_by_remaining_measured_mirrors(tmp_path, mock_repo):
    class BrokenChunks(_QuietHandler):
        def do_GET(self):
            if urlparse(self.path).path.endswith("P"):
                self.send_error(503)
            else:
                super().do_GET()

    class Working(_QuietHandler):
        def do_GET(self):
            if urlparse(self.path).path.endswith("C"):
                time.sleep(0.05)
            super().do_GET()

    servers = [_start_server(mock_repo, BrokenChunks) for _ in range(5)]
    servers.append(_start_server(mock_repo, Working))
    try:
        proc, config = run_select(tmp_path, " ".join(base for _, base in servers))
        assert proc.returncode == 0, proc.stdout
        assert _configured_server_urls(config) == [servers[-1][1] + "/cvmfs/@fqrn@"]
    finally:
        for server, _ in servers:
            server.shutdown()
            server.server_close()
