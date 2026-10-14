"""Behaviour of the helper that refreshes managed providers in opencode.json.

The OpenCode wrapper calls this helper after probing Jetstream, Ollama and
llm.neurodesk.org. Wrapper-level contracts live in ``test_coding_agents.py``;
these tests drive the module and its CLI directly.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from testlib import load_source_module, repo_path, resolve_source

ouc = load_source_module(
    "opencode_update_config",
    "/opt/neurodesktop/opencode_update_config.py",
    "config/agents/opencode_update_config.py",
)

INSTALLED_PATH = "/opt/neurodesktop/opencode_update_config.py"


def detected(**models):
    """Detected models keyed by provider, with default endpoints."""
    return ouc.DetectedModels(models=models)


def shipped_defaults():
    path = resolve_source(
        "/opt/jovyan_defaults/.config/opencode/opencode.json",
        "config/agents/opencode_config.json",
    )
    return json.loads(path.read_text(encoding="utf-8"))


# --- update_config -----------------------------------------------------------


def test_missing_models_take_metadata_from_the_image_defaults():
    cfg = ouc.update_config(
        {"provider": {}}, shipped_defaults(), detected(neurodesk=["neurodesk"])
    )

    assert cfg["provider"]["neurodesk"]["models"] == {
        "neurodesk": {"name": "neurodesk", "limit": {"context": 131000, "output": 8192}}
    }
    # The image default names the provider "Neurodesk LiteLLM"; the refresh
    # owns that label.
    assert cfg["provider"]["neurodesk"]["name"] == "Neurodesk LLMs"


def test_user_model_settings_win_over_image_defaults():
    user = {
        "provider": {
            "jetstream": {"models": {"gpt-oss-120b": {"name": "Mine", "x": 1}}}
        }
    }

    cfg = ouc.update_config(user, shipped_defaults(), detected(jetstream=["gpt-oss-120b"]))

    assert cfg["provider"]["jetstream"]["models"] == {
        "gpt-oss-120b": {"name": "Mine", "x": 1}
    }


def test_unmanaged_providers_and_top_level_keys_are_untouched():
    user = {
        "theme": "dark",
        "mcp": {"brain-researcher": {"enabled": True}},
        "provider": {"jetstream-deepseek": {"models": {"DeepSeek-R1": {}}}},
    }

    cfg = ouc.update_config(user, shipped_defaults(), detected())

    assert cfg["theme"] == "dark"
    assert cfg["mcp"] == {"brain-researcher": {"enabled": True}}
    assert cfg["provider"]["jetstream-deepseek"] == {"models": {"DeepSeek-R1": {}}}


def test_input_config_is_not_mutated():
    user = {"instructions": ["/opt/AGENTS.md"], "provider": {}}
    snapshot = json.loads(json.dumps(user))

    ouc.update_config(user, {}, detected(jetstream=["a"]))

    assert user == snapshot


@pytest.mark.parametrize(
    ("instructions", "expected"),
    [
        (["/opt/AGENTS.md", "./mine.md"], ["./mine.md"]),
        ([" /opt/AGENTS.md\n"], None),
        (["./mine.md", 3], ["./mine.md", 3]),
    ],
)
def test_only_the_legacy_global_instruction_is_dropped(instructions, expected):
    cfg = ouc.update_config({"instructions": instructions}, {}, detected())

    assert cfg.get("instructions") == expected


def test_a_non_list_instructions_value_is_left_alone():
    cfg = ouc.update_config({"instructions": "/opt/AGENTS.md"}, {}, detected())

    assert cfg["instructions"] == "/opt/AGENTS.md"


def test_providers_absent_everywhere_use_builtin_definitions():
    models = ouc.DetectedModels(
        models={"neurodesk": ["m"]},
        base_urls={
            "jetstream": "https://js.example/v1",
            "ollama": "http://ollama.example:11434/v1",
            "neurodesk": "https://neurodesk.example/openai",
        },
    )

    cfg = ouc.update_config({}, {}, models)

    assert cfg["provider"] == {
        "jetstream": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "JetStream",
            "options": {"baseURL": "https://js.example/v1"},
            "models": {},
        },
        "ollama": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Local Ollama",
            "options": {"baseURL": "http://ollama.example:11434/v1"},
            "models": {},
        },
        "neurodesk": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Neurodesk LLMs",
            "options": {
                "baseURL": "https://neurodesk.example/openai",
                "apiKey": "{env:NEURODESK_API_KEY}",
            },
            "models": {"m": {"name": "m"}},
        },
    }
    assert cfg["model"] == "neurodesk/m"


def test_malformed_provider_entries_are_rebuilt():
    user = {
        "provider": {
            "jetstream": "broken",
            "ollama": {"options": ["broken"], "models": "broken"},
            "neurodesk": {"options": {"apiKey": "{env:OTHER}"}, "models": []},
        }
    }

    cfg = ouc.update_config(
        user, {}, detected(jetstream=["j"], ollama=["o"], neurodesk=["n", ""])
    )

    providers = cfg["provider"]
    assert providers["jetstream"]["models"] == {"j": {"name": "j"}}
    assert providers["ollama"]["options"] == {"baseURL": "http://127.0.0.1:11434/v1"}
    assert providers["ollama"]["models"]["o"]["limit"] == {
        "context": 16384,
        "output": 8192,
    }
    assert providers["neurodesk"]["options"]["apiKey"] == "{env:OTHER}"
    assert providers["neurodesk"]["models"] == {"n": {"name": "n"}}


def test_a_non_object_provider_section_is_replaced():
    cfg = ouc.update_config({"provider": ["broken"]}, {}, detected(jetstream=["j"]))

    assert set(cfg["provider"]) == {"jetstream", "ollama", "neurodesk"}


@pytest.mark.parametrize(
    ("user_limit", "probed", "expected"),
    [
        # User limits win, but output is capped to the context window.
        ({"context": 4096, "output": 99999}, 65536, {"context": 4096, "output": 4096}),
        # The probed context fills a missing user value.
        ({}, 65536, {"context": 65536, "output": 1000}),
        # Garbage everywhere falls back to the defaults.
        ({"context": "abc", "output": 0}, -5, {"context": 2048, "output": 1000}),
        ({"context": float("inf"), "output": None}, "x", {"context": 2048, "output": 1000}),
        # Numeric strings are accepted.
        ({"context": "8000", "output": "500"}, None, {"context": 8000, "output": 500}),
        # The output default is capped by a small context too.
        ({"context": 512}, None, {"context": 512, "output": 512}),
    ],
)
def test_ollama_limits(user_limit, probed, expected):
    user = {
        "provider": {
            "ollama": {"models": {"m": {"name": "m", "limit": user_limit, "keep": 1}}}
        }
    }
    models = ouc.DetectedModels(
        models={"ollama": ["m"]},
        ollama_contexts={"m": probed},
        ollama_default_context=2048,
        ollama_default_output=1000,
    )

    cfg = ouc.update_config(user, {}, models)

    model = cfg["provider"]["ollama"]["models"]["m"]
    assert model["limit"] == expected
    assert model["keep"] == 1


def test_selected_model_becomes_the_default_when_known():
    cfg = ouc.update_config(
        {"model": "jetstream/a"},
        {},
        detected(jetstream=["a"], neurodesk=["org/model"]),
        "neurodesk/org/model",
    )

    assert cfg["model"] == "neurodesk/org/model"


def test_unknown_selection_keeps_a_valid_current_default():
    cfg = ouc.update_config(
        {"model": "neurodesk/b"},
        {},
        detected(jetstream=["a"], neurodesk=["b"]),
        "neurodesk/missing",
    )

    assert cfg["model"] == "neurodesk/b"


@pytest.mark.parametrize("current", ["neurodesk/gone", "no-slash", None, 7])
def test_invalid_default_moves_to_the_first_managed_provider_with_models(current):
    cfg = ouc.update_config(
        {"model": current}, {}, detected(ollama=["o1", "o2"], neurodesk=["n"])
    )

    assert cfg["model"] == "ollama/o1"


def test_a_default_in_an_unmanaged_provider_is_kept():
    user = {
        "model": "anthropic/claude",
        "provider": {"anthropic": {"models": {"claude": {}}}},
    }

    cfg = ouc.update_config(user, {}, detected(jetstream=["a"]))

    assert cfg["model"] == "anthropic/claude"


def test_no_detected_models_leave_an_invalid_default_unchanged():
    cfg = ouc.update_config({"model": "neurodesk/gone"}, {}, detected())

    assert cfg["model"] == "neurodesk/gone"


# --- CLI -----------------------------------------------------------------------


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-I", str(ouc.SOURCE_PATH), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "opencode.json"
    path.write_text(json.dumps({"model": "neurodesk/old", "theme": "x"}), "utf-8")
    return path


def test_cli_updates_the_config_atomically_and_keeps_its_mode(tmp_path, config_path):
    config_path.chmod(0o600)

    result = run_cli(
        config_path,
        tmp_path / "missing-defaults.json",
        "--select=neurodesk/-dash-model",
        "--neurodesk-model=-dash-model",
        "--ollama-model=o",
        "--ollama-contexts={\"o\": 4096}",
        "--ollama-default-context=32768",
        "--ollama-default-output=8192",
        "--ollama-base-url=http://127.0.0.1:9/v1",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    text = config_path.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    cfg = json.loads(text)
    assert cfg["theme"] == "x"
    assert cfg["model"] == "neurodesk/-dash-model"
    assert cfg["provider"]["ollama"]["models"]["o"]["limit"] == {
        "context": 4096,
        "output": 4096,
    }
    assert cfg["provider"]["ollama"]["options"]["baseURL"] == "http://127.0.0.1:9/v1"
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["opencode.json"]


def test_cli_repeated_runs_are_stable(tmp_path, config_path):
    args = (config_path, tmp_path / "none.json", "--jetstream-model=a")

    assert run_cli(*args).returncode == 0
    first = config_path.read_bytes()
    assert run_cli(*args).returncode == 0

    assert config_path.read_bytes() == first


@pytest.mark.parametrize("contexts", ["", "not json", "[1, 2]", '{"o": 4096}}'])
def test_cli_ignores_unusable_probed_contexts(tmp_path, config_path, contexts):
    result = run_cli(
        config_path,
        tmp_path / "none.json",
        "--ollama-model=o",
        f"--ollama-contexts={contexts}",
        "--ollama-default-context=bogus",
        "--ollama-default-output=-1",
    )

    assert result.returncode == 0, result.stderr
    limit = json.loads(config_path.read_text("utf-8"))["provider"]["ollama"][
        "models"
    ]["o"]["limit"]
    assert limit == {"context": 16384, "output": 8192}


SECRET = "sk-very-secret-provider-key"


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        ('{"provider": {"neurodesk": {"options": {"apiKey": "' + SECRET + '"', "valid JSON"),
        (json.dumps([SECRET]), "JSON object"),
        (b"\xff\xfe" + SECRET.encode(), "valid JSON"),
    ],
)
def test_cli_rejects_an_unusable_config_without_writing(tmp_path, contents, reason):
    path = tmp_path / "opencode.json"
    if isinstance(contents, bytes):
        path.write_bytes(contents)
    else:
        path.write_text(contents, encoding="utf-8")
    before = path.read_bytes()

    result = run_cli(path, tmp_path / "none.json", "--jetstream-model=a")

    assert result.returncode == 1
    assert result.stdout == ""
    assert reason in result.stderr
    assert result.stderr.startswith("opencode_update_config: ")
    assert result.stderr.count("\n") == 1
    assert "Traceback" not in result.stderr
    assert SECRET not in result.stderr
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["opencode.json"]


def test_cli_reports_a_missing_config(tmp_path):
    result = run_cli(tmp_path / "opencode.json", tmp_path / "none.json")

    assert result.returncode == 1
    assert "cannot read" in result.stderr
    assert not (tmp_path / "opencode.json").exists()


def test_cli_rejects_corrupt_defaults_without_writing(tmp_path, config_path):
    defaults = tmp_path / "defaults.json"
    defaults.write_text("{", encoding="utf-8")
    before = config_path.read_bytes()

    result = run_cli(config_path, defaults, "--jetstream-model=a")

    assert result.returncode == 1
    assert str(defaults) in result.stderr
    assert config_path.read_bytes() == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_cli_reports_an_unwritable_directory(tmp_path):
    config_dir = tmp_path / "opencode"
    config_dir.mkdir()
    path = config_dir / "opencode.json"
    path.write_text("{}", encoding="utf-8")
    config_dir.chmod(0o500)
    try:
        result = run_cli(path, tmp_path / "none.json", "--jetstream-model=a")
    finally:
        config_dir.chmod(0o700)

    assert result.returncode == 1
    assert "cannot write" in result.stderr
    assert path.read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in config_dir.iterdir()) == ["opencode.json"]


# --- packaging -----------------------------------------------------------------


def test_dockerfile_installs_the_helper_where_the_wrapper_calls_it():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    wrapper = repo_path("config/agents/opencode").read_text(encoding="utf-8")

    assert (
        "--mount=type=bind,source=config/agents/opencode_update_config.py,"
        "target=/tmp/agents/opencode_update_config.py,ro"
    ) in dockerfile
    assert (
        "install -m 0755 -o root -g users /tmp/agents/opencode_update_config.py "
        f"{INSTALLED_PATH}"
    ) in dockerfile
    assert f'OPENCODE_UPDATE_CONFIG_HELPER="{INSTALLED_PATH}"' in wrapper
    assert re.search(r'python3 "\$\{OPENCODE_UPDATE_CONFIG_HELPER\}"', wrapper)


def test_the_wrapper_keeps_no_inline_copy_of_the_config_update():
    wrapper = repo_path("config/agents/opencode").read_text(encoding="utf-8")

    for marker in ("def apply_models", "def ensure_provider", "JETSTREAM_MODELS_JSON"):
        assert marker not in wrapper


def test_helper_is_in_the_formatting_and_strict_typing_scopes():
    relative = "config/agents/opencode_update_config.py"

    assert relative in repo_path("scripts/check_quality.py").read_text("utf-8")
    assert relative in repo_path("mypy.ini").read_text("utf-8")
    assert os.access(Path(ouc.SOURCE_PATH), os.X_OK)
