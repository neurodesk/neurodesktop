import http.server
import json
import os
import subprocess
import threading
import pytest

from testlib import resolve_source


def nbi_setup_script_path():
    return resolve_source(
        "/opt/neurodesktop/nbi_setup.sh", "config/agents/nbi_setup.sh"
    )


def nbi_default_config_path():
    return resolve_source(
        "/opt/jovyan_defaults/.jupyter/nbi/config.json",
        "config/agents/nbi_config.json",
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


# Python modules nbi_setup.sh loads from its own directory.
NBI_SETUP_HELPERS = ("provider_security.py",)


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
    for helper in NBI_SETUP_HELPERS:
        (tmp_path / helper).write_text(resolve_source(
            f"/opt/neurodesktop/{helper}", f"config/agents/{helper}"
        ).read_text())

    return test_script, home_dir


def run_nbi_setup(test_script, home_dir, *args):
    env = {**os.environ, "HOME": str(home_dir)}
    env.pop("NEURODESK_API_KEY", None)
    env.pop("BR_MCP_TOKEN", None)
    # Keep the running-server refresh hermetic: only jpserver files placed
    # into the sandbox home by a test may be contacted.
    env.pop("JUPYTER_RUNTIME_DIR", None)
    process = subprocess.run(
        ["bash", str(test_script), *args],
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


@pytest.mark.parametrize("existing", [None, "managed", "user"])
def test_home_claude_guidance_points_to_shared_contract(tmp_path, existing):
    script, home = make_nbi_setup_sandbox(tmp_path)
    target = home / "CLAUDE.md"
    marker = "<!-- neurodesktop:nbi-rules (managed - do not edit) -->"
    original = "# My instructions\nKeep my project conventions.\n"
    if existing == "managed":
        target.write_text(marker + "\nAlways use !fslmaths and ask for a decision.\n")
    elif existing == "user":
        target.write_text(original)

    run_nbi_setup(script, home, "--no-refresh")
    result = target.read_text()
    if existing == "user":
        assert result == original
    else:
        assert result.startswith(marker + "\n")
        assert "/opt/AGENTS.md" in result
        assert "workspace AGENTS.md or CLAUDE.md" in result
        assert "!fslmaths" not in result
        assert "ask for a decision" not in result
    run_nbi_setup(script, home, "--no-refresh")
    assert target.read_text() == result


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


@pytest.mark.parametrize("endpoint", [
    "https://llm.neurodesk.org.attacker.invalid/openai",
    "https://attacker.invalid/llm.neurodesk.org",
    "https://llm.neurodesk.org@attacker.invalid/openai",
    "http://llm.neurodesk.org/openai",
    "https://llm.neurodesk.org:8443/openai",
    "https://user@llm.neurodesk.org/openai",
])
def test_nbi_does_not_inject_neurodesk_key_into_untrusted_endpoint(tmp_path, endpoint):
    script, home = make_nbi_setup_sandbox(tmp_path)
    config_path = home / ".jupyter/nbi/config.json"
    config_path.parent.mkdir(parents=True)
    config = json.loads(nbi_default_config_path().read_text())
    for name in ("chat_model", "inline_completion_model"):
        for prop in config[name]["properties"]:
            if prop["id"] == "base_url":
                prop["value"] = endpoint
            if prop["id"] == "api_key":
                prop["value"] = "custom-key"
    config_path.write_text(json.dumps(config))
    write_bashrc_api_key(home, "private-neurodesk-key")
    run_nbi_setup(script, home)
    result = read_nbi_config(home)
    for name in ("chat_model", "inline_completion_model"):
        assert get_prop(result[name], "base_url") == endpoint
        assert get_prop(result[name], "api_key") == "custom-key"


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


def test_nbi_provider_change_cannot_carry_neurodesk_key_to_other_host(tmp_path):
    script, home = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home, "neurodesk/model-alpha")
    write_bashrc_api_key(home, "private-neurodesk-key")
    run_nbi_setup(script, home)
    path = home / ".config/opencode/opencode.json"
    config = json.loads(path.read_text())
    config["provider"]["neurodesk"]["options"]["baseURL"] = "https://llm.neurodesk.org.attacker.invalid/openai"
    path.write_text(json.dumps(config))
    run_nbi_setup(script, home)
    for name in ("chat_model", "inline_completion_model"):
        assert get_prop(read_nbi_config(home)[name], "api_key") == ""


@pytest.mark.parametrize("endpoint", ["https://llm.neurodesk.org/openai", "https://LLM.NEURODESK.ORG:443/openai"])
def test_nbi_accepts_exact_https_authority(tmp_path, endpoint):
    script, home = make_nbi_setup_sandbox(tmp_path)
    path = home / ".jupyter/nbi/config.json"
    path.parent.mkdir(parents=True)
    config = json.loads(nbi_default_config_path().read_text())
    for name in ("chat_model", "inline_completion_model"):
        for prop in config[name]["properties"]:
            if prop["id"] == "base_url":
                prop["value"] = endpoint
    path.write_text(json.dumps(config))
    write_bashrc_api_key(home, "private-neurodesk-key")
    run_nbi_setup(script, home)
    for name in ("chat_model", "inline_completion_model"):
        assert get_prop(read_nbi_config(home)[name], "api_key") == "private-neurodesk-key"


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


def test_nbi_default_uses_gateway_stable_alias(tmp_path):
    """A fresh home targets llm.neurodesk.org's maintained `neurodesk` alias."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)

    run_nbi_setup(test_script, home_dir, "--no-refresh")

    cfg = read_nbi_config(home_dir)
    for section_name in ("chat_model", "inline_completion_model"):
        assert get_prop(cfg[section_name], "model_id") == "neurodesk"


def write_nbi_sections(home_dir, base_url, model_id):
    nbi_dir = home_dir / ".jupyter/nbi"
    nbi_dir.mkdir(parents=True)
    cfg = {}
    for section_name in ("chat_model", "inline_completion_model"):
        cfg[section_name] = {
            "provider": "openai-compatible",
            "model": f"openai-compatible-{section_name}",
            "properties": [
                {"id": "api_key", "value": "existing-key"},
                {"id": "model_id", "value": model_id},
                {"id": "base_url", "value": base_url},
                {"id": "context_window", "value": "64000"},
            ],
        }
    (nbi_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


def test_nbi_migrates_legacy_seeded_model_on_gateway(tmp_path):
    """The model earlier images seeded is replaced; other settings survive."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_nbi_sections(home_dir, "https://llm.neurodesk.org/openai", "kimi-k2.5")

    output = run_nbi_setup(test_script, home_dir, "--no-refresh")

    assert "repaired" in output
    cfg = read_nbi_config(home_dir)
    for section_name in ("chat_model", "inline_completion_model"):
        section = cfg[section_name]
        assert get_prop(section, "model_id") == "neurodesk"
        assert get_prop(section, "base_url") == "https://llm.neurodesk.org/openai"
        assert get_prop(section, "api_key") == "existing-key"
        assert get_prop(section, "context_window") == "64000"


@pytest.mark.parametrize(
    ("base_url", "model_id"),
    [
        ("https://llm.neurodesk.org/openai", "qwen3.5-122b"),
        ("https://my-own-endpoint.example/v1", "kimi-k2.5"),
    ],
)
def test_nbi_keeps_user_model_choices(tmp_path, base_url, model_id):
    """Only the legacy default on the gateway is migrated."""
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_nbi_sections(home_dir, base_url, model_id)

    run_nbi_setup(test_script, home_dir, "--no-refresh")

    cfg = read_nbi_config(home_dir)
    for section_name in ("chat_model", "inline_completion_model"):
        assert get_prop(cfg[section_name], "model_id") == model_id


def test_boot_sync_writes_config_without_contacting_running_server(tmp_path):
    test_script, home_dir = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home_dir, "jetstream/gpt-oss-120b")
    server = start_fake_jupyter_server()
    try:
        write_jpserver_runtime_file(
            home_dir, f"http://127.0.0.1:{server.server_address[1]}/", "secret-token"
        )
        run_nbi_setup(test_script, home_dir, "--no-refresh")
        assert server.recorded_requests == []
        assert "gpt-oss-120b" in json.dumps(read_nbi_config(home_dir))
        assert (home_dir / ".jupyter/nbi/mcp.json").is_file()
    finally:
        server.shutdown()
        server.server_close()


# --- Mirroring the OpenCode selection: wrapper contract ----------------------


def run_nbi_setup_split(test_script, home_dir, env_extra=None):
    """Run nbi_setup.sh as boot does and keep stdout and stderr apart."""
    env = {**os.environ, "HOME": str(home_dir)}
    for name in ("NEURODESK_API_KEY", "BR_MCP_TOKEN", "JUPYTER_RUNTIME_DIR"):
        env.pop(name, None)
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(test_script), "--no-refresh"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def nbi_config_path(home_dir):
    return home_dir / ".jupyter/nbi/config.json"


def write_raw_opencode_config(home_dir, data):
    config_dir = home_dir / ".config/opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "opencode.json"
    path.write_text(
        data if isinstance(data, str) else json.dumps(data), encoding="utf-8"
    )
    return path


def write_nbi_config(home_dir, cfg):
    path = nbi_config_path(home_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg if isinstance(cfg, str) else json.dumps(cfg), encoding="utf-8")
    return path


def section_with(base_url, model_id="old-model", api_key="old-key", **extra):
    props = [
        {"id": "api_key", "value": api_key},
        {"id": "model_id", "value": model_id},
        {"id": "base_url", "value": base_url},
    ]
    props += [{"id": key, "value": value} for key, value in extra.items()]
    return {
        "provider": "openai-compatible",
        "model": "openai-compatible-chat-model",
        "properties": props,
    }


def copied_providers():
    return json.loads(json.dumps(OPENCODE_PROVIDERS))


def test_mirror_keeps_user_settings_and_is_stable_on_rerun(tmp_path):
    script, home = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home, "jetstream/gpt-oss-120b")
    chat = section_with("https://llm.neurodesk.org/openai", api_key="", temperature="0.2")
    chat["properties"].append({"id": "custom", "value": "kept", "note": "x"})
    chat["user_flag"] = True
    write_nbi_config(home, {
        "chat_model": chat,
        "inline_completion_model": section_with("https://llm.jetstream-cloud.org/v1/"),
        "embedding_model": {"provider": "mine"},
        "mcp_server_settings": {"keep": [1, 2]},
    })

    first = run_nbi_setup_split(script, home)

    assert first.returncode == 0, first.stderr
    assert first.stdout == ""
    assert (
        "nbi_setup.sh: synced Notebook Intelligence to OpenCode selection "
        "jetstream/gpt-oss-120b\n"
    ) in first.stderr
    path = nbi_config_path(home)
    written = path.read_bytes()
    assert written.endswith(b"}\n")
    assert path.stat().st_mode & 0o777 == 0o600
    assert not path.with_name("config.json.tmp").exists()
    cfg = json.loads(written)
    assert cfg["embedding_model"] == {"provider": "mine"}
    assert cfg["mcp_server_settings"] == {"keep": [1, 2]}
    assert cfg["chat_model"]["user_flag"] is True
    assert {"id": "custom", "value": "kept", "note": "x"} in cfg["chat_model"]["properties"]
    assert get_prop(cfg["chat_model"], "temperature") == "0.2"
    for name in ("chat_model", "inline_completion_model"):
        assert get_prop(cfg[name], "base_url") == "https://llm.jetstream-cloud.org/v1"
        assert get_prop(cfg[name], "model_id") == "gpt-oss-120b"
        assert get_prop(cfg[name], "context_window") is None
    # Moving to JetStream, which has no key, clears the old endpoint's key; a
    # trailing slash does not make the same endpoint look different.
    assert get_prop(cfg["chat_model"], "api_key") == ""
    assert get_prop(cfg["inline_completion_model"], "api_key") == "old-key"
    # Existing properties are updated in place, keeping their order.
    assert [p["id"] for p in cfg["chat_model"]["properties"]][:3] == [
        "api_key", "model_id", "base_url",
    ]

    second = run_nbi_setup_split(script, home)

    assert second.returncode == 0
    assert "synced Notebook Intelligence" not in second.stderr
    assert path.read_bytes() == written


def test_mirror_keeps_the_existing_key_on_the_same_endpoint(tmp_path):
    script, home = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home, "jetstream/gpt-oss-120b")
    write_nbi_config(home, {
        "chat_model": section_with("https://llm.jetstream-cloud.org/v1", api_key="js-key"),
        "inline_completion_model": section_with(
            "https://llm.jetstream-cloud.org/v1", api_key="js-key"
        ),
    })

    result = run_nbi_setup_split(script, home)

    assert result.returncode == 0
    cfg = read_nbi_config(home)
    for name in ("chat_model", "inline_completion_model"):
        assert get_prop(cfg[name], "api_key") == "js-key"
        assert get_prop(cfg[name], "model_id") == "gpt-oss-120b"


def test_mirror_resolves_other_env_placeholders_without_printing_them(tmp_path):
    script, home = make_nbi_setup_sandbox(tmp_path)
    providers = copied_providers()
    providers["jetstream"]["options"]["apiKey"] = "{env:JETSTREAM_TOKEN}"
    providers["jetstream"]["models"]["gpt-oss-120b"]["limit"] = {"context": "65536"}
    write_raw_opencode_config(
        home, {"model": "jetstream/gpt-oss-120b", "provider": providers}
    )

    result = run_nbi_setup_split(
        script, home, env_extra={"JETSTREAM_TOKEN": "js-secret-value"}
    )

    assert result.returncode == 0
    assert "js-secret-value" not in result.stdout + result.stderr
    cfg = read_nbi_config(home)
    for name in ("chat_model", "inline_completion_model"):
        assert get_prop(cfg[name], "api_key") == "js-secret-value"
        assert get_prop(cfg[name], "context_window") == "65536"


def test_mirror_never_sends_the_neurodesk_key_to_another_host(tmp_path):
    script, home = make_nbi_setup_sandbox(tmp_path)
    providers = copied_providers()
    providers["jetstream"]["options"]["apiKey"] = "{env:NEURODESK_API_KEY}"
    write_raw_opencode_config(
        home, {"model": "jetstream/gpt-oss-120b", "provider": providers}
    )
    write_bashrc_api_key(home, "private-neurodesk-key")

    result = run_nbi_setup_split(script, home)

    assert result.returncode == 0
    assert "private-neurodesk-key" not in result.stdout + result.stderr
    cfg = read_nbi_config(home)
    for name in ("chat_model", "inline_completion_model"):
        assert get_prop(cfg[name], "base_url") == "https://llm.jetstream-cloud.org/v1"
        assert get_prop(cfg[name], "api_key") == ""


@pytest.mark.parametrize(
    "opencode",
    [
        "{not json",
        "[]",
        {"provider": OPENCODE_PROVIDERS},
        {"model": "no-slash", "provider": OPENCODE_PROVIDERS},
        {"model": "jetstream/", "provider": OPENCODE_PROVIDERS},
        {"model": "missing/model", "provider": OPENCODE_PROVIDERS},
        {"model": "jetstream/gpt-oss-120b", "provider": []},
        {
            "model": "ollama/llama3",
            "provider": {
                "ollama": {
                    "npm": "ollama-ai-provider",
                    "options": {"baseURL": "http://localhost:11434"},
                }
            },
        },
        {"model": "bare/m", "provider": {"bare": {"npm": "@ai-sdk/openai-compatible"}}},
    ],
    ids=[
        "corrupt", "array", "no-model", "no-slash", "empty-model", "unknown-provider",
        "providers-not-object", "not-openai-compatible", "no-base-url",
    ],
)
def test_mirror_skips_unusable_opencode_selections(tmp_path, opencode):
    script, home = make_nbi_setup_sandbox(tmp_path)
    write_raw_opencode_config(home, opencode)
    path = write_nbi_config(home, {
        "chat_model": section_with("https://llm.jetstream-cloud.org/v1"),
        "inline_completion_model": section_with("https://llm.jetstream-cloud.org/v1"),
    })
    before = path.read_bytes()

    result = run_nbi_setup_split(script, home)

    assert result.returncode == 0
    assert result.stdout == ""
    assert "synced Notebook Intelligence" not in result.stderr
    assert "Traceback" not in result.stderr
    assert path.read_bytes() == before


def test_mirror_leaves_a_corrupt_nbi_config_untouched(tmp_path):
    script, home = make_nbi_setup_sandbox(tmp_path)
    write_opencode_config(home, "jetstream/gpt-oss-120b")
    corrupt = '{"chat_model": {"api_key": "do-not-echo"'
    path = write_nbi_config(home, corrupt)

    result = run_nbi_setup_split(script, home)

    assert result.returncode == 0
    assert result.stdout == ""
    assert "do-not-echo" not in result.stderr
    assert path.read_text() == corrupt


def test_mirror_fills_missing_properties_and_ignores_bad_context_limits(tmp_path):
    script, home = make_nbi_setup_sandbox(tmp_path)
    providers = copied_providers()
    providers["jetstream"]["models"]["gpt-oss-120b"]["limit"] = {"context": "lots"}
    write_raw_opencode_config(
        home, {"model": "jetstream/gpt-oss-120b", "provider": providers}
    )
    section = {
        "provider": "openai-compatible",
        "properties": [
            "not-a-property",
            {"id": "base_url", "value": "https://llm.jetstream-cloud.org/v1"},
        ],
    }
    write_nbi_config(home, {"chat_model": section, "inline_completion_model": section})

    result = run_nbi_setup_split(script, home)

    assert result.returncode == 0
    cfg = read_nbi_config(home)
    for name in ("chat_model", "inline_completion_model"):
        # No key is invented on the same endpoint, and a non-numeric context
        # limit is not copied.
        assert cfg[name]["properties"] == [
            "not-a-property",
            {"id": "base_url", "value": "https://llm.jetstream-cloud.org/v1"},
            {"id": "model_id", "value": "gpt-oss-120b"},
        ]
