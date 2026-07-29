"""OpenCode contracts that only the built image can answer.

The proxy-rewriting, path-confinement and work-directory logic is unit-tested
in ``tests/unit/test_opencode_web.py`` against an imported module and fake
backends. What is left here needs the real thing: the pinned OpenCode bundle
shipped at /usr/bin/opencode, and the image's Lmod installation.
"""

import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from testlib import resolve_source


def opencode_bash_env_path():
    """Locate the non-interactive OpenCode Bash initializer."""
    return resolve_source(
        "/opt/neurodesktop/opencode_bash_env.sh", "config/agents/opencode_bash_env.sh"
    )


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port, timeout=20):
    """Block until the port accepts connections or fail the test."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"port {port} did not open in time")


# --- Lmod-backed Bash contract --------------------------------------------------

def _require_lmod_image():
    """The image always installs Lmod; its absence is a build regression."""
    assert Path("/etc/profile.d/lmod.sh").is_file(), (
        "/etc/profile.d/lmod.sh is missing — the image did not install Lmod"
    )


def _write_test_module(module_root):
    """Create a small local module for the OpenCode shell contract tests."""
    module_file = module_root / "modules" / "opencode-test" / "1.0.lua"
    module_file.parent.mkdir(parents=True)
    module_file.write_text(
        'setenv("NEURODESKTOP_OPENCODE_MODULE_TEST", "loaded")\n',
        encoding="utf-8",
    )


def _opencode_shell_test_env(tmp_path):
    """Model the stale environment inherited from lazy-started Jupyter."""
    _require_lmod_image()
    local_containers = tmp_path / "local-containers"
    _write_test_module(local_containers)
    return {
        **os.environ,
        "BASH_ENV": str(opencode_bash_env_path()),
        "NEURODESKTOP_LOCAL_CONTAINERS": str(local_containers),
        "NEURODESKTOP_ENV_SOURCED": "1",
        "CVMFS_DISABLE": "true",
        "MODULEPATH": str(tmp_path / "stale-modulepath"),
    }


def test_opencode_bash_env_loads_modules_in_noninteractive_shell(tmp_path):
    """Every OpenCode Bash tool refreshes MODULEPATH and initializes Lmod."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            "module load opencode-test/1.0 && "
            'test "$NEURODESKTOP_OPENCODE_MODULE_TEST" = loaded',
        ],
        env=_opencode_shell_test_env(tmp_path),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_opencode_bash_env_rejects_missing_modules(tmp_path):
    """A failed module load remains fatal and cannot produce downstream output."""
    marker = tmp_path / "must-not-exist"
    env = _opencode_shell_test_env(tmp_path)
    env["OPENCODE_MODULE_TEST_MARKER"] = str(marker)
    result = subprocess.run(
        [
            "bash",
            "-c",
            "module load funny-name-tool && "
            'printf created > "$OPENCODE_MODULE_TEST_MARKER"',
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not marker.exists()


# --- Pinned bundle contract -----------------------------------------------------


def test_pinned_opencode_bundle_supports_model_picker_and_home_sessions(tmp_path):
    """The image's real pinned UI and session API must honor proxy contracts.

    This is the version-coupled contract that the fake-backend tests cannot
    prove: OpenCode must still read the default-server localStorage key and
    ship the provider route plus its native model-selection interface, and its
    session API must preserve the distinct directory and project identity
    selected by the proxy so every workspace remains visible on Home.
    """
    assert Path("/usr/bin/opencode").is_file(), (
        "/usr/bin/opencode is missing — the image did not install the pinned bundle"
    )

    port = _free_port()
    password = "bundle-contract-password"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "OPENCODE_SERVER_PASSWORD": password,
    }
    process = subprocess.Popen(
        [
            "/usr/bin/opencode", "web", "--hostname", "127.0.0.1",
            "--port", str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    auth = {
        "Authorization": "Basic "
        + base64.b64encode(f"opencode:{password}".encode()).decode()
    }
    try:
        _wait_for_port(port)
        deadline = time.monotonic() + 30
        status = 0
        root = ""
        while time.monotonic() < deadline:
            try:
                status, _headers, root = _request(
                    port, "/", headers=auth, timeout=3
                )
                if status == 200:
                    break
            except (OSError, TimeoutError):
                if process.poll() is not None:
                    break
            time.sleep(0.2)
        assert status == 200

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text("# Session instructions\n", encoding="utf-8")
        session_dirs = [
            Path(
                ocw.create_opencode_work_dir(
                    tmp_path,
                    "20260728_120000",
                    agents_file=agents_file,
                )
            )
            for _ in range(2)
        ]
        created_sessions = []
        for session_dir in session_dirs:
            status, _headers, body = _request(
                port,
                "/session",
                headers={
                    **auth,
                    "Content-Type": "application/json",
                    "x-opencode-directory": urllib.parse.quote(
                        str(session_dir), safe=""
                    ),
                },
                data=b"{}",
            )
            assert status == 200
            session = json.loads(body)
            assert session["id"].startswith("ses_")
            assert session["directory"] == str(session_dir)
            created_sessions.append(session)
        assert (
            created_sessions[0]["directory"]
            != created_sessions[1]["directory"]
        )

        status, _headers, body = _request(port, "/project", headers=auth)
        assert status == 200
        projects = json.loads(body)
        expected_worktrees = {str(directory) for directory in session_dirs}
        session_projects = [
            project
            for project in projects
            if project["worktree"] in expected_worktrees
        ]
        assert len(session_projects) == 2
        assert len({project["id"] for project in session_projects}) == 2
        assert all(project["id"] != "global" for project in session_projects)

        status, _headers, body = _request(
            port, "/api/session?limit=5000&order=desc", headers=auth
        )
        assert status == 200
        indexed_sessions = json.loads(body)["data"]
        project_directories = {
            directory
            for project in projects
            for directory in [project["worktree"], *project.get("sandboxes", [])]
        }
        home_session_ids = {
            session["id"]
            for session in indexed_sessions
            if session["location"]["directory"] in project_directories
        }
        assert {session["id"] for session in created_sessions} <= home_session_ids

        match = re.search(r'src="([^"]+/index-[^"]+\.js)"', root)
        assert match, root

        status, _headers, bundle = _request(
            port, match.group(1), headers=auth
        )
        assert status == 200
        assert "opencode.settings.dat:defaultServerUrl" in bundle
        assert 'url:"/provider"' in bundle
        assert "model.select" in bundle
        canonical_origin = (
            'location.hostname.includes("opencode.ai")?'
            '"http://localhost:4096":location.origin'
        )
        assert bundle.count(canonical_origin) == 1
        protocol_probe_url = "new URL(n,e.url)"
        v2_request_url = "new URL(a.path,e.baseUrl)"
        assert bundle.count(protocol_probe_url) == 1
        assert bundle.count(v2_request_url) == 1
        router_matches = re.findall(
            r"get component\(\)\{return e\.router\?\?"
            r"([A-Za-z_$][A-Za-z0-9_$]*)\},root:n=>",
            bundle,
        )
        assert len(router_matches) == 1
        router_component = (
            "get component(){return e.router??"
            f"{router_matches[0]}"
            "},root:n=>"
        )
        route_parser = (
            '=(e,t)=>{const n=e.split("/").filter(Boolean);'
            'if(n.length===0)return{type:"home"}'
        )
        assert bundle.count(route_parser) == 1
        proxy_prefix = "/opencode"
        proxied_bundle = ocw.rewrite_js(bundle, proxy_prefix)
        assert canonical_origin not in proxied_bundle
        assert protocol_probe_url not in proxied_bundle
        assert v2_request_url not in proxied_bundle
        assert "window.__NEURODESK_OPENCODE_SERVER_URL__" in proxied_bundle
        assert router_component not in proxied_bundle
        assert "window.__NEURODESK_OPENCODE_BASE_PATH__" in proxied_bundle
        assert route_parser not in proxied_bundle
        assert (
            '_nb&&(e===_nb||e.startsWith(_nb+"/"))&&(e=e.slice(_nb.length))'
            in proxied_bundle
        )
        assert 'location.assign("/opencode/")' in proxied_bundle
        assert 'b.set("Accept","text/event-stream")' in proxied_bundle
        assert 'url:"/api/session"' in proxied_bundle
        assert f'url:"{proxy_prefix}/api/session"' not in proxied_bundle
        assert '"assets/row-' in bundle
        assert '"assets/row-' not in proxied_bundle
        assert f'"{proxy_prefix}/assets/row-' in proxied_bundle
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
