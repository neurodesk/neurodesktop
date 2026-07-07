import json
import os
import subprocess
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
