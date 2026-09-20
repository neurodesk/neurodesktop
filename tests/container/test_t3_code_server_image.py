"""Installed-image checks for the T3 Code server."""

from __future__ import annotations

import os
import json
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import time

from testlib import load_source_module


def platform_package():
    """The one self-contained build the install layer keeps for this image."""
    builds = sorted(Path("/opt/t3-code/node_modules/@t3code").glob("t3-*"))
    assert len(builds) == 1, builds
    return builds[0]


def test_t3_code_runtime_and_native_terminal_support_are_installed():
    assert subprocess.check_output(["t3", "--version"], text=True).strip() == "t3 v0.0.42"
    major, minor, *_ = map(int, subprocess.check_output(["node", "--version"], text=True).strip()[1:].split("."))
    assert (major, minor) >= (24, 10)
    assert shutil.which("codex")
    assert shutil.which("claude")
    assert shutil.which("opencode")
    assert "2026.9.1" in subprocess.check_output(["cloudflared", "--version"], text=True)

    build = platform_package()
    machine = {"x86_64": "x64", "aarch64": "arm64"}[os.uname().machine]
    assert build.name == f"t3-linux-{machine}"
    # The executable, its web client and its PTY binding ship together; the
    # server loads all three from this directory.
    assert os.access(build / "t3", os.X_OK)
    assert (build / "client/index.html").is_file()
    subprocess.run(
        [
            "node",
            "-e",
            f"const p=require('{build}/node_modules/node-pty');"
            "const x=p.spawn('/bin/sh',['-c','exit 0']);"
            "x.onExit(({exitCode})=>process.exit(exitCode));",
        ],
        check=True,
        timeout=10,
    )


def test_t3_code_image_ships_one_platform_build_and_no_build_leftovers():
    build = platform_package()
    # A prebuilt, bundled tree: nothing is compiled and no source maps ship.
    assert not Path.home().joinpath(".cache/node-gyp").exists()
    assert not list(Path("/opt/t3-code").rglob("*.js.map"))
    assert (build / "node_modules/node-pty/build/Release/pty.node").is_file()
    assert os.access("/opt/neurodesktop/t3-provider-bin/codex", os.X_OK)
    assert os.access("/opt/neurodesktop/t3-provider-bin/claude", os.X_OK)
    assert os.access("/opt/neurodesktop/t3-provider-bin/opencode", os.X_OK)


def test_real_t3_server_starts_on_loopback_with_private_state(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    # An obsolete home install must not win after T3 hydrates the login PATH.
    old_bin = tmp_path / ".local/bin"
    old_bin.mkdir(parents=True)
    old_codex = old_bin / "codex"
    old_codex.write_text("#!/bin/sh\necho obsolete-home-codex >&2\nexit 1\n")
    old_codex.chmod(0o755)
    (tmp_path / ".bash_profile").write_text(f'export PATH="{old_bin}:$PATH"\n')
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(tmp_path),
            "T3CODE_HOME": str(tmp_path / ".t3"),
            "PATH": f"/opt/neurodesktop/t3-provider-bin:{environment['PATH']}",
            "T3CODE_LOG_LEVEL": "Warn",
            "T3CODE_TRACE_MIN_LEVEL": "Warn",
            "T3CODE_TRACE_FILE": "/dev/null",
        }
    )
    from neurodesk_t3_code import supervisor
    from types import SimpleNamespace
    supervisor.seed_provider_settings(SimpleNamespace(
        base_dir=tmp_path / ".t3", provider_bin=Path("/opt/neurodesktop/t3-provider-bin")
    ))
    process = subprocess.Popen(
        [
            "t3",
            "serve",
            "--mode",
            "web",
            "--no-browser",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--base-dir",
            str(tmp_path / ".t3"),
            str(tmp_path),
        ],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"T3 exited during startup with {process.returncode}")
            with socket.socket() as client:
                client.settimeout(0.2)
                if client.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("T3 did not listen within 20 seconds")

        # Server startup reloads the login-shell PATH, which can put the
        # interactive Codex wrapper ahead of the quiet provider directory.
        # A listening port alone misses protocol-breaking wrapper banners.
        cache = tmp_path / ".t3/caches/codex.json"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert process.poll() is None, "T3 exited before its provider probe completed"
            if cache.exists():
                snapshot = json.loads(cache.read_text())
                if snapshot.get("version") or snapshot.get("status") == "error":
                    assert snapshot["installed"] is True
                    assert snapshot["version"] is not None, snapshot.get("message")
                    assert "decode-wire-message" not in (snapshot.get("message") or "")
                    assert "decode-payload" not in (snapshot.get("message") or ""), snapshot
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("T3 did not finish its Codex provider probe within 30 seconds")
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)

    assert (tmp_path / ".t3/userdata").is_dir()


def test_guided_setup_mints_a_pairing_link_from_the_installed_cli(tmp_path):
    """Guard the CLI contract the wizard prints its pairing link from.

    The wizard has T3 build the link against the tailnet address, because
    `t3 pair` builds one for the container's own address, which no desktop can
    reach. A pinned-version bump that renames these flags or changes the JSON
    would otherwise surface only during a manual desktop pairing.
    """
    wizard = load_source_module(
        "t3_setup", "/opt/neurodesktop/t3_neurodesk_setup.py",
        "scripts/t3_neurodesk_setup.py",
    )
    base_dir = tmp_path / ".t3"
    endpoint = "https://neurodesktop.example-tail.ts.net"
    link = wizard.pairing_url(shutil.which("t3"), base_dir, endpoint)
    code = re.fullmatch(rf"{endpoint}/pair#token=([A-Z0-9]{{8,32}})", link)
    assert code, link
    listed = subprocess.run(
        ["t3", "--log-level=warn", "auth", "pairing", "list",
         "--base-dir", str(base_dir), "--json"],
        capture_output=True, text=True, timeout=60, check=True,
    )
    assert any(entry["label"] == "Desktop app" for entry in json.loads(listed.stdout))


def test_t3_connect_uses_image_relay_instead_of_cached_download(tmp_path):
    from neurodesk_t3_code import supervisor
    from types import SimpleNamespace

    base = tmp_path / ".t3"
    machine = {"x86_64": "x64", "aarch64": "arm64"}[os.uname().machine]
    cached = base / "tools/cloudflared/2026.5.2" / f"linux-{machine}" / "cloudflared"
    cached.parent.mkdir(parents=True)
    cached.write_text("#!/bin/sh\nexit 99\n")
    cached.chmod(0o755)
    policy = SimpleNamespace(home=tmp_path, base_dir=base, host="127.0.0.1", port=3773,
                             provider_bin=Path("/opt/neurodesktop/t3-provider-bin"))
    environment = supervisor.server_environment(policy, os.environ)
    result = subprocess.run(["t3", "connect", "status", "--json", "--base-dir", str(base)],
                            env=environment, capture_output=True, text=True, check=True, timeout=30)
    relay = json.loads(result.stdout)["relayClient"]
    assert relay["source"] == "override"
    assert relay["executablePath"] == "/usr/local/bin/cloudflared"
    assert relay["status"] == "available"
