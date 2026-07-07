import http.server
import json
import os
import subprocess
import threading
from pathlib import Path


def first_existing_path(*candidates):
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
    raise AssertionError(f"None of these paths exist: {candidates}")


def nbi_setup_script_path():
    return first_existing_path(
        "/opt/neurodesktop/nbi_setup.sh",
        Path(__file__).resolve().parents[1] / "config/agents/nbi_setup.sh",
    )


def nbi_default_config_path():
    return first_existing_path(
        "/opt/jovyan_defaults/.jupyter/nbi/config.json",
        Path(__file__).resolve().parents[1] / "config/agents/nbi_config.json",
    )


OPENCODE_PROVIDERS = {
    "neurodesk": {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Neurodesk LLMs",
        "options": {
            "baseURL": "https://llm.neurodesk.org/openai",
            "apiKey": "{env:NEURODESK_API_KEY}",
        },
        "models": {
            "model-alpha": {
                "name": "model-alpha",
                "limit": {"context": 131000, "output": 8192},
            }
        },
    },
    "jetstream": {
        "npm": "@ai-sdk/openai-compatible",
        "name": "JetStream",
        "options": {"baseURL": "https://llm.jetstream-cloud.org/v1"},
        "models": {"gpt-oss-120b": {"name": "gpt-oss-120b"}},
    },
}


def make_nbi_setup_sandbox(tmp_path):
    """Copy nbi_setup.sh with /opt defaults redirected into tmp_path."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    default_config = tmp_path / "nbi-default-config.json"
    default_config.write_text(
        nbi_default_config_path().read_text(encoding="utf-8"), encoding="utf-8"
    )
    default_mcp = tmp_path / "nbi-default-mcp.json"
    default_mcp.write_text('{"mcpServers": {}}\n', encoding="utf-8")

    script_contents = nbi_setup_script_path().read_text(encoding="utf-8")
    script_contents = script_contents.replace(
        'NBI_DEFAULT_CONFIG="/opt/jovyan_defaults/.jupyter/nbi/config.json"',
        f'NBI_DEFAULT_CONFIG="{default_config}"',
    )
    script_contents = script_contents.replace(
        'NBI_DEFAULT_MCP="/opt/jovyan_defaults/.jupyter/nbi/mcp.json"',
        f'NBI_DEFAULT_MCP="{default_mcp}"',
    )
    test_script = tmp_path / "nbi_setup_test.sh"
    test_script.write_text(script_contents, encoding="utf-8")
    test_script.chmod(0o755)

    return test_script, home_dir


def run_nbi_setup(test_script, home_dir):
    env = {**os.environ, "HOME": str(home_dir)}
    env.pop("NEURODESK_API_KEY", None)
    env.pop("BR_MCP_TOKEN", None)
    # Keep the running-server refresh hermetic: only jpserver files placed
    # into the sandbox home by a test may be contacted.
    env.pop("JUPYTER_RUNTIME_DIR", None)
    process = subprocess.run(
        ["bash", str(test_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        timeout=30,
    )
    assert process.returncode == 0, f"nbi_setup.sh failed: {process.stdout}"
    return process.stdout


def write_opencode_config(home_dir, model):
    config_dir = home_dir / ".config/opencode"
    config_dir.mkdir(parents=True)
    (config_dir / "opencode.json").write_text(
        json.dumps({"model": model, "provider": OPENCODE_PROVIDERS}),
        encoding="utf-8",
    )


def write_bashrc_api_key(home_dir, key):
    (home_dir / ".bashrc").write_text(
        f"export NEURODESK_API_KEY='{key}'\n", encoding="utf-8"
    )


def read_nbi_config(home_dir):
    return json.loads(
        (home_dir / ".jupyter/nbi/config.json").read_text(encoding="utf-8")
    )


def get_prop(section, prop_id):
    for prop in section.get("properties", []):
        if prop.get("id") == prop_id:
            return prop.get("value")
    return None


def test_nbi_follows_opencode_jetstream_selection(tmp_path):
    """Selecting a JetStream model in OpenCode retargets both NBI models."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home_dir, "jetstream/gpt-oss-120b")

    run_nbi_setup(test_script, home_dir)

    cfg = read_nbi_config(home_dir)
    for section_name in ("chat_model", "inline_completion_model"):
        section = cfg[section_name]
        assert section["provider"] == "openai-compatible"
        assert get_prop(section, "base_url") == "https://llm.jetstream-cloud.org/v1"
        assert get_prop(section, "model_id") == "gpt-oss-120b"


def test_nbi_follows_opencode_neurodesk_selection_with_key(tmp_path):
    """A Neurodesk selection carries model id, API key, and context window."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home_dir, "neurodesk/model-alpha")
    write_bashrc_api_key(home_dir, "neurodesk-test-key")

    run_nbi_setup(test_script, home_dir)

    cfg = read_nbi_config(home_dir)
    for section_name in ("chat_model", "inline_completion_model"):
        section = cfg[section_name]
        assert get_prop(section, "base_url") == "https://llm.neurodesk.org/openai"
        assert get_prop(section, "model_id") == "model-alpha"
        assert get_prop(section, "api_key") == "neurodesk-test-key"
        assert get_prop(section, "context_window") == "131000"


def test_nbi_sync_runs_again_after_key_rotation(tmp_path):
    """A new key in ~/.bashrc replaces the stale key on the next run."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home_dir, "neurodesk/model-alpha")
    write_bashrc_api_key(home_dir, "old-key")
    run_nbi_setup(test_script, home_dir)

    write_bashrc_api_key(home_dir, "rotated-key")
    run_nbi_setup(test_script, home_dir)

    cfg = read_nbi_config(home_dir)
    for section_name in ("chat_model", "inline_completion_model"):
        assert get_prop(cfg[section_name], "api_key") == "rotated-key"


def test_nbi_custom_endpoint_left_alone(tmp_path):
    """NBI sections pointed at an unmanaged endpoint are not overwritten."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home_dir, "jetstream/gpt-oss-120b")

    nbi_dir = home_dir / ".jupyter/nbi"
    nbi_dir.mkdir(parents=True)
    custom_section = {
        "provider": "openai-compatible",
        "model": "openai-compatible-chat-model",
        "properties": [
            {"id": "api_key", "value": "my-own-key"},
            {"id": "model_id", "value": "my-own-model"},
            {"id": "base_url", "value": "https://my-own-endpoint.example/v1"},
            {"id": "context_window", "value": "8000"},
        ],
    }
    (nbi_dir / "config.json").write_text(
        json.dumps(
            {
                "chat_model": custom_section,
                "inline_completion_model": json.loads(json.dumps(custom_section)),
            }
        ),
        encoding="utf-8",
    )

    run_nbi_setup(test_script, home_dir)

    cfg = read_nbi_config(home_dir)
    for section_name in ("chat_model", "inline_completion_model"):
        section = cfg[section_name]
        assert get_prop(section, "base_url") == "https://my-own-endpoint.example/v1"
        assert get_prop(section, "model_id") == "my-own-model"
        assert get_prop(section, "api_key") == "my-own-key"


def test_nbi_non_openai_provider_left_alone(tmp_path):
    """Only openai-compatible sections are synced; others stay untouched."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home_dir, "jetstream/gpt-oss-120b")

    default_cfg = json.loads(nbi_default_config_path().read_text(encoding="utf-8"))
    default_cfg["chat_model"] = {"provider": "claude", "model": "claude-chat-model"}
    nbi_dir = home_dir / ".jupyter/nbi"
    nbi_dir.mkdir(parents=True)
    (nbi_dir / "config.json").write_text(json.dumps(default_cfg), encoding="utf-8")

    run_nbi_setup(test_script, home_dir)

    cfg = read_nbi_config(home_dir)
    assert cfg["chat_model"] == {"provider": "claude", "model": "claude-chat-model"}
    inline_section = cfg["inline_completion_model"]
    assert get_prop(inline_section, "base_url") == "https://llm.jetstream-cloud.org/v1"
    assert get_prop(inline_section, "model_id") == "gpt-oss-120b"


class _RecordingNBIHandler(http.server.BaseHTTPRequestHandler):
    """Fake Jupyter server that records NBI refresh requests."""

    requests = None  # set per-instance via server attribute

    def _record_and_reply(self, method):
        self.server.recorded_requests.append(
            {
                "method": method,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
            }
        )
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._record_and_reply("GET")

    def do_POST(self):
        self._record_and_reply("POST")

    def log_message(self, *args):
        pass


def start_fake_jupyter_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _RecordingNBIHandler)
    server.recorded_requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def write_jpserver_runtime_file(home_dir, url, token):
    runtime_dir = home_dir / ".local/share/jupyter/runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "jpserver-123.json").write_text(
        json.dumps({"url": url, "token": token}), encoding="utf-8"
    )


def test_nbi_refreshes_running_jupyter_server(tmp_path):
    """After a sync, a live Jupyter server is asked to re-read the config."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home_dir, "jetstream/gpt-oss-120b")

    server = start_fake_jupyter_server()
    try:
        port = server.server_address[1]
        write_jpserver_runtime_file(
            home_dir, f"http://127.0.0.1:{port}/", "secret-token"
        )

        output = run_nbi_setup(test_script, home_dir)
    finally:
        server.shutdown()

    capability_requests = [
        req
        for req in server.recorded_requests
        if req["path"] == "/notebook-intelligence/capabilities"
    ]
    assert len(capability_requests) == 1, server.recorded_requests
    assert capability_requests[0]["method"] == "GET"
    assert capability_requests[0]["authorization"] == "token secret-token"
    # The brain-researcher MCP entry did not change, so MCP connections
    # are not churned.
    assert not any(
        req["path"] == "/notebook-intelligence/reload-mcp-servers"
        for req in server.recorded_requests
    )
    assert "refreshed Notebook Intelligence in 1 running" in output


def test_nbi_reloads_mcp_servers_when_br_token_appears(tmp_path):
    """A new BR_MCP_TOKEN also triggers a live MCP server reload."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home_dir, "jetstream/gpt-oss-120b")
    (home_dir / ".bashrc").write_text(
        "export BR_MCP_TOKEN='br-test-token'\n", encoding="utf-8"
    )

    server = start_fake_jupyter_server()
    try:
        port = server.server_address[1]
        write_jpserver_runtime_file(
            home_dir, f"http://127.0.0.1:{port}/", "secret-token"
        )

        run_nbi_setup(test_script, home_dir)
    finally:
        server.shutdown()

    reload_requests = [
        req
        for req in server.recorded_requests
        if req["path"] == "/notebook-intelligence/reload-mcp-servers"
    ]
    assert len(reload_requests) == 1, server.recorded_requests
    assert reload_requests[0]["method"] == "POST"
    assert reload_requests[0]["authorization"] == "token secret-token"


def test_nbi_refresh_tolerates_dead_server(tmp_path):
    """Stale jpserver files pointing at dead ports do not fail the sync."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home_dir, "jetstream/gpt-oss-120b")

    # Grab a free port, then close it again so nothing is listening.
    probe = http.server.HTTPServer(("127.0.0.1", 0), _RecordingNBIHandler)
    dead_port = probe.server_address[1]
    probe.server_close()
    write_jpserver_runtime_file(
        home_dir, f"http://127.0.0.1:{dead_port}/", "secret-token"
    )

    output = run_nbi_setup(test_script, home_dir)

    assert "refreshed Notebook Intelligence" not in output
    cfg = read_nbi_config(home_dir)
    assert get_prop(cfg["chat_model"], "model_id") == "gpt-oss-120b"


def test_nbi_defaults_kept_without_opencode_config(tmp_path):
    """Without an OpenCode config the seeded default still gets the API key."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_bashrc_api_key(home_dir, "neurodesk-test-key")

    run_nbi_setup(test_script, home_dir)

    default_cfg = json.loads(nbi_default_config_path().read_text(encoding="utf-8"))
    default_model_id = get_prop(default_cfg["chat_model"], "model_id")

    cfg = read_nbi_config(home_dir)
    for section_name in ("chat_model", "inline_completion_model"):
        section = cfg[section_name]
        assert get_prop(section, "base_url") == "https://llm.neurodesk.org/openai"
        assert get_prop(section, "model_id") == default_model_id
        assert get_prop(section, "api_key") == "neurodesk-test-key"
