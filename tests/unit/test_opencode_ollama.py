from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.server
import json
import os
import subprocess
import threading
import time

import pytest

from testlib import load_source_module, repo_path

ouc = load_source_module(
    "opencode_ollama_config",
    "/opt/neurodesktop/opencode_update_config.py",
    "config/agents/opencode_update_config.py",
)


@contextmanager
def ollama_server(tags=None, show=None, delay=0):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            self.respond(
                {"models": [{"name": "actual:latest"}]} if tags is None else tags
            )

        def do_POST(self):
            requests.append((self.path, dict(self.headers)))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            time.sleep(delay)
            self.respond(
                {"model_info": {"llama.context_length": 4096}} if show is None else show
            )

        def respond(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("suffix", ["", "/", "/v1", "/v1/"])
def test_discovery_normalizes_endpoint_and_never_sends_provider_tokens(
    suffix, monkeypatch
):
    monkeypatch.setenv("NEURODESK_API_KEY", "neurodesk-private")
    monkeypatch.setenv("BR_MCP_TOKEN", "br-private")
    with ollama_server() as (host, calls):
        observed = ouc.discover_ollama(host + suffix)
    assert observed.base_url == host + "/v1"
    assert observed.contexts == {"actual:latest": 4096}
    assert [path for path, _ in calls] == ["/api/tags", "/api/show"]
    assert all("Authorization" not in headers for _, headers in calls)
    assert "private" not in json.dumps(calls)


@pytest.mark.parametrize(
    "tags", [{}, {"models": None}, {"models": [{"name": "bad\nmodel"}]}, []]
)
def test_failed_listing_differs_from_a_successful_empty_catalog(tags):
    with ollama_server(tags=tags) as (host, _):
        assert ouc.discover_ollama(host) is None
    with ollama_server(tags={"models": []}) as (host, _):
        assert ouc.discover_ollama(host).contexts == {}


@pytest.mark.parametrize(
    "metadata, expected",
    [
        (
            {
                "parameters": "num_ctx 2048",
                "model_info": {"llama.context_length": 99999},
            },
            2048,
        ),
        ({"modelfile": "PARAMETER num_ctx 1024"}, 1024),
        ({"details": {"max_sequence_length": "8192"}}, 8192),
        ({}, 32768),
    ],
)
def test_model_context_and_metadata_fallback(metadata, expected):
    with ollama_server(show=metadata) as (host, _):
        assert ouc.discover_ollama(host).contexts == {"actual:latest": expected}


def test_deadline_covers_all_model_metadata_requests():
    tags = {"models": [{"name": f"m{i}"} for i in range(30)]}
    with ollama_server(tags=tags, delay=1) as (host, calls):
        started = time.monotonic()
        snapshot = ouc.discover_ollama(host, deadline_seconds=0.2)
        elapsed = time.monotonic() - started
    assert elapsed < 0.8
    assert len(snapshot.contexts) == 30
    assert set(snapshot.contexts.values()) == {32768}
    assert len(calls) == 2


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://example.org",
        "http://user:secret@example.org",
        "https://example.org?token=secret",
        "http://x:bad",
        "not a URL",
        "http://example.org/{env:NEURODESK_API_KEY}",
        "http://example.org/{file:/tmp/private}",
    ],
)
def test_discovery_rejects_credentialed_and_invalid_endpoints(endpoint):
    assert ouc.discover_ollama(endpoint) is None


@pytest.fixture
def quiet_launcher(tmp_path):
    plugin = tmp_path / "ollama-config.mjs"
    plugin.write_text(
        repo_path("config/agents/opencode_ollama_config.js")
        .read_text()
        .replace(
            "/opt/neurodesktop/opencode_update_config.py",
            str(repo_path("config/agents/opencode_update_config.py")),
        )
    )
    driver = tmp_path / "config-driver.mjs"
    driver.write_text("""import { OllamaConfig } from './ollama-config.mjs';
const config = JSON.parse(process.env.TEST_RESOLVED_CONFIG || '{}');
const hooks = await OllamaConfig({});
if (hooks.config) await hooks.config(config);
process.stdout.write(JSON.stringify(config));
""")
    binary = tmp_path / "opencode-real"
    binary.write_text(f"""#!/usr/bin/env python3
import json,os,sys,subprocess
resolved = subprocess.check_output(["node", {str(driver)!r}],text=True)
print(json.dumps({{"argv":sys.argv[1:],"inline":os.environ.get("OPENCODE_CONFIG_CONTENT"),"resolved":json.loads(resolved)}}))
""")
    binary.chmod(0o755)
    defaults = repo_path("config/agents/opencode_config.json")
    launcher = tmp_path / "quiet-launcher"
    source = repo_path("config/agents/t3-provider-bin/opencode").read_text()
    source = (
        source.replace("/usr/bin/opencode", str(binary))
        .replace(
            "/opt/neurodesktop/opencode_update_config.py",
            str(repo_path("config/agents/opencode_update_config.py")),
        )
        .replace("/opt/jovyan_defaults/.config/opencode/opencode.json", str(defaults))
    )
    launcher.write_text(source)
    launcher.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return launcher, home


def run_quiet(launcher, home, args=("serve",), **variables):
    env = {**os.environ, "HOME": str(home), **variables}
    result = subprocess.run(
        [str(launcher), *args],
        env=env,
        cwd=home,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_fresh_quiet_serve_discovers_without_global_writes_or_setup(quiet_launcher):
    launcher, home = quiet_launcher
    with ollama_server() as (host, calls):
        result = run_quiet(launcher, home, OLLAMA_HOST=host)
    provider = result["resolved"]["provider"]["ollama"]
    assert provider["models"]["actual:latest"]["limit"] == {
        "context": 4096,
        "output": 4096,
    }
    assert provider["whitelist"] == ["actual:latest"]
    assert result["argv"] == ["serve"]
    assert len(calls) == 2
    assert not list(home.iterdir())


def test_version_bypasses_discovery_and_all_setup(quiet_launcher):
    launcher, home = quiet_launcher
    with ollama_server() as (host, calls):
        result = run_quiet(launcher, home, ("--version",), OLLAMA_HOST=host)
    assert result["argv"] == ["--version"]
    assert result["inline"] is None
    assert calls == []
    assert not list(home.iterdir())


@pytest.mark.parametrize(
    "inline",
    [
        '{"provider":{"ollama":{"options":{"baseURL":"http://mine/v1"}}}}',
        "{broken",
        '{// JSONC caller\n"model":"ollama/mine"}',
    ],
)
def test_explicit_inline_provider_and_unparsed_inline_content_survive(
    quiet_launcher, inline
):
    launcher, home = quiet_launcher
    with ollama_server() as (host, _):
        result = run_quiet(
            launcher, home, OLLAMA_HOST=host, OPENCODE_CONFIG_CONTENT=inline
        )
    assert result["inline"] == inline


def test_custom_resolved_provider_survives(quiet_launcher):
    launcher, home = quiet_launcher
    resolved = {
        "provider": {
            "ollama": {"npm": "my-provider", "options": {"baseURL": "https://mine/v1"}}
        }
    }
    with ollama_server() as (host, _):
        result = run_quiet(
            launcher, home, OLLAMA_HOST=host, TEST_RESOLVED_CONFIG=json.dumps(resolved)
        )
    assert result["inline"] is None
    assert result["resolved"] == resolved


def test_offline_preserves_saved_configs_and_inline_settings(quiet_launcher):
    launcher, home = quiet_launcher
    config = home / "opencode.json"
    config.write_text('{"model":"ollama/saved"}')
    inline = '{"instructions":["rules.md"],"model":"ollama/chosen"}'
    result = run_quiet(
        launcher, home, OLLAMA_HOST="http://127.0.0.1:1", OPENCODE_CONFIG_CONTENT=inline
    )
    assert result["inline"] == inline
    assert config.read_text() == '{"model":"ollama/saved"}'


def test_concurrent_quiet_launches_have_independent_observations(quiet_launcher):
    launcher, home = quiet_launcher
    with ollama_server() as (host, _), ThreadPoolExecutor() as pool:
        results = list(
            pool.map(lambda _: run_quiet(launcher, home, OLLAMA_HOST=host), range(4))
        )
    assert len({json.dumps(result["resolved"]) for result in results}) == 1
    assert not list(home.iterdir())


def test_overlay_hides_only_the_image_seed_and_retains_user_limits_and_defaults():
    defaults = json.loads(repo_path("config/agents/opencode_config.json").read_text())
    existing = json.loads(json.dumps(defaults))
    existing["model"] = "ollama/saved"
    existing["provider"]["ollama"]["models"]["saved"] = {
        "name": "Mine",
        "limit": {"context": 1024, "output": 512},
        "temperature": 0.3,
    }
    snapshot = ouc.OllamaSnapshot(
        "http://host.docker.internal:11434/v1", {"saved": 8192, "fresh": 2048}
    )
    delta = ouc.quiet_ollama_overlay(snapshot, existing, defaults)
    provider = delta["provider"]["ollama"]
    assert provider["whitelist"] == ["saved", "fresh"]
    assert provider["models"]["saved"] == {"limit": {"context": 1024, "output": 512}}
    assert "model" not in delta
    assert existing["provider"]["ollama"]["models"]["saved"]["temperature"] == 0.3


def test_observing_only_ollama_preserves_unobserved_providers_and_selected_default():
    existing = {
        "model": "ollama/offline-choice",
        "provider": {
            "neurodesk": {"models": {"n": {}}},
            "ollama": {"models": {"old": {}}},
        },
    }
    updated = ouc.update_config(existing, {}, ouc.DetectedModels(models={"ollama": []}))
    assert updated["provider"]["neurodesk"] == existing["provider"]["neurodesk"]
    assert updated["provider"]["ollama"]["models"] == {}
    assert updated["model"] == "ollama/offline-choice"


def test_curl_user_settings_cannot_attach_credentials(tmp_path, monkeypatch):
    (tmp_path / ".curlrc").write_text(
        'header = "Authorization: Bearer private-token"\n'
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CURL_HOME", str(tmp_path))
    with ollama_server() as (host, calls):
        assert ouc.discover_ollama(host).contexts == {"actual:latest": 4096}
    assert all("Authorization" not in headers for _, headers in calls)


def test_model_ids_remain_data_at_the_shell_boundary(quiet_launcher):
    launcher, home = quiet_launcher
    model = "-model;$(touch injected)"
    with ollama_server(tags={"models": [{"name": model}]}) as (host, _):
        result = run_quiet(launcher, home, OLLAMA_HOST=host)
    assert model in result["resolved"]["provider"]["ollama"]["models"]
    assert not (home / "injected").exists()


def test_interactive_refresh_preserves_custom_ollama_provider():
    provider = {
        "npm": "@ai-sdk/openai-compatible",
        "options": {"baseURL": "https://my-private-ollama.example/v1"},
        "models": {"chosen": {"name": "Chosen"}},
    }
    updated = ouc.update_config(
        {"model": "ollama/chosen", "provider": {"ollama": provider}},
        {},
        ouc.DetectedModels(models={"ollama": ["other"]}),
    )
    assert updated["provider"]["ollama"] == provider
    assert updated["model"] == "ollama/chosen"


@pytest.mark.parametrize(
    "tags",
    [
        {"models": [{"name": "x" * 513}]},
        {"models": [{"name": f"model-{i}"} for i in range(257)]},
        {"models": [], "oversized": "x" * 2097152},
    ],
)
def test_discovery_bounds_model_ids_catalog_and_response_size(tags):
    with ollama_server(tags=tags) as (host, _):
        assert ouc.discover_ollama(host) is None


@pytest.mark.parametrize(
    "name", ["probe-{env:NEURODESK_API_KEY}", "probe-{file:/private}"]
)
def test_discovered_interpolation_is_rejected_before_show_and_config(name):
    with ollama_server(tags={"models": [{"name": name}]}) as (host, calls):
        assert ouc.discover_ollama(host) is None
    assert [path for path, _ in calls] == ["/api/tags"]


@pytest.mark.parametrize("field", ["model", "endpoint"])
def test_interactive_refresh_rejects_interpolation_in_observed_values(field):
    existing = {
        "provider": {"ollama": {"models": {"saved": {"name": "{env:USER_NAME}"}}}}
    }
    models = ouc.DetectedModels(models={"ollama": ["fresh"]})
    if field == "model":
        models.models["ollama"] = ["{env:NEURODESK_API_KEY}"]
    else:
        models.base_urls["ollama"] = "http://example.org/{file:/private}"
    assert ouc.update_config(existing, {}, models)["provider"] == existing["provider"]


def test_ollama_config_plugin_is_installed_with_home_defaults():
    dockerfile = repo_path("Dockerfile").read_text()
    assert (
        "--mount=type=bind,source=config/agents/opencode_ollama_config.js,target=/tmp/agents/opencode_ollama_config.js,ro"
        in dockerfile
    )
    assert (
        "install -D -m 0644 /tmp/agents/opencode_ollama_config.js /opt/jovyan_defaults/.config/opencode/plugins/ollama-config.js"
        in dockerfile
    )
