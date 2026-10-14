"""Behaviour of the helper that mirrors the OpenCode model selection into NBI.

``nbi_setup.sh`` calls this helper. Wrapper-level contracts live in
``test_nbi_opencode_sync.py``; these tests drive the module and its CLI
directly.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from testlib import load_source_module, repo_path, resolve_source

INSTALLED_PATH = "/opt/neurodesktop/nbi_mirror_opencode.py"
HELPER_PATH = resolve_source(INSTALLED_PATH, "config/agents/nbi_mirror_opencode.py")
# The helper imports provider_security from its own directory, as it does
# when Python runs it as a script.
sys.path.insert(0, str(HELPER_PATH.parent))
mirror = load_source_module(
    "nbi_mirror_opencode", INSTALLED_PATH, "config/agents/nbi_mirror_opencode.py"
)

NEURODESK_URL = "https://llm.neurodesk.org/openai"
JETSTREAM_URL = "https://llm.jetstream-cloud.org/v1"


def opencode(model="jetstream/gpt-oss-120b", **provider_overrides):
    providers = {
        "neurodesk": {
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": NEURODESK_URL, "apiKey": "{env:NEURODESK_API_KEY}"},
            "models": {"model-alpha": {"limit": {"context": 131000}}},
        },
        "jetstream": {
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": JETSTREAM_URL + "/"},
            "models": {"gpt-oss-120b": {}},
        },
    }
    providers.update(provider_overrides)
    return {"model": model, "provider": providers}


def select(cfg, key="", environ=None):
    return mirror.read_selection(cfg, key, environ or {})


def section(base_url, model_id="old", api_key="old-key", provider="openai-compatible"):
    return {
        "provider": provider,
        "properties": [
            {"id": "api_key", "value": api_key},
            {"id": "model_id", "value": model_id},
            {"id": "base_url", "value": base_url},
        ],
    }


def props(cfg, name):
    return {p["id"]: p["value"] for p in cfg[name]["properties"]}


# --- read_selection ----------------------------------------------------------


def test_selection_strips_the_trailing_slash_and_lists_managed_urls():
    selection = select(opencode())

    assert selection.label == "jetstream/gpt-oss-120b"
    assert selection.base_url == JETSTREAM_URL
    assert selection.api_key == ""
    assert selection.context_window == ""
    assert selection.managed_urls == {NEURODESK_URL, JETSTREAM_URL}


def test_neurodesk_url_stays_managed_when_opencode_drops_the_provider():
    cfg = opencode()
    del cfg["provider"]["neurodesk"]

    assert NEURODESK_URL in select(cfg).managed_urls


def test_model_ids_keep_everything_after_the_first_slash():
    cfg = opencode(model="jetstream/org/model:tag")

    assert select(cfg).model_id == "org/model:tag"


def test_neurodesk_key_resolves_only_for_the_exact_gateway():
    assert select(opencode("neurodesk/model-alpha"), "nd-key").api_key == "nd-key"

    hostile = opencode(
        "neurodesk/model-alpha",
        neurodesk={
            "npm": "@ai-sdk/openai-compatible",
            "options": {
                "baseURL": "https://llm.neurodesk.org.attacker.invalid/openai",
                "apiKey": "{env:NEURODESK_API_KEY}",
            },
        },
    )
    assert select(hostile, "nd-key").api_key == ""


def test_other_placeholders_resolve_from_the_environment_and_literals_pass():
    cfg = opencode()
    cfg["provider"]["jetstream"]["options"]["apiKey"] = "{env:JS_TOKEN}"
    assert select(cfg, environ={"JS_TOKEN": "js"}).api_key == "js"
    assert select(cfg).api_key == ""

    cfg["provider"]["jetstream"]["options"]["apiKey"] = "literal-key"
    assert select(cfg).api_key == "literal-key"


@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        ({"context": 131000}, "131000"),
        ({"context": "65536"}, "65536"),
        ({"context": 4096.9}, "4096"),
        ({"context": 0}, ""),
        ({"context": -5}, ""),
        ({"context": "lots"}, ""),
        ({"context": float("inf")}, ""),
        ({"context": float("nan")}, ""),
        ({"context": True}, ""),
        ({"context": None}, ""),
        ({"context": [1]}, ""),
        ({}, ""),
        ("not-a-dict", ""),
    ],
)
def test_context_window_needs_a_positive_number(limit, expected):
    cfg = opencode()
    cfg["provider"]["jetstream"]["models"]["gpt-oss-120b"] = {"limit": limit}

    assert select(cfg).context_window == expected


@pytest.mark.parametrize(
    "cfg",
    [
        None,
        [],
        {"provider": {}},
        opencode(model=7),
        opencode(model="no-slash"),
        opencode(model="jetstream/"),
        opencode(model="missing/m"),
        {"model": "jetstream/gpt-oss-120b", "provider": []},
        opencode(jetstream="not-an-object"),
        opencode(jetstream={"npm": "ollama-ai-provider", "options": {"baseURL": "x"}}),
        opencode(jetstream={"npm": "@ai-sdk/openai-compatible"}),
        opencode(jetstream={"npm": "@ai-sdk/openai-compatible", "options": "x"}),
    ],
)
def test_unusable_selections_are_none(cfg):
    assert select(cfg) is None


# --- apply_selection ---------------------------------------------------------


def test_managed_sections_follow_the_selection_without_mutating_input():
    nbi = {
        "chat_model": section(NEURODESK_URL),
        "inline_completion_model": section(NEURODESK_URL),
        "other": {"keep": True},
    }
    before = json.loads(json.dumps(nbi))

    updated = mirror.apply_selection(nbi, select(opencode("neurodesk/model-alpha"), "k"))

    assert nbi == before
    assert updated["other"] == {"keep": True}
    for name in mirror.NBI_SECTIONS:
        assert props(updated, name) == {
            "api_key": "k",
            "model_id": "model-alpha",
            "base_url": NEURODESK_URL,
            "context_window": "131000",
        }


def test_unchanged_config_returns_none():
    nbi = {"chat_model": section(JETSTREAM_URL, "gpt-oss-120b", "")}

    assert mirror.apply_selection(nbi, select(opencode())) is None


@pytest.mark.parametrize(
    "chat",
    [
        section("https://my-own-endpoint.example/v1"),
        section(NEURODESK_URL, provider="claude"),
        "not-an-object",
    ],
    ids=["unmanaged-endpoint", "other-provider", "not-an-object"],
)
def test_hand_configured_sections_are_left_alone(chat):
    nbi = {"chat_model": chat, "inline_completion_model": section(NEURODESK_URL)}

    updated = mirror.apply_selection(nbi, select(opencode()))

    assert updated["chat_model"] == chat
    assert props(updated, "inline_completion_model")["base_url"] == JETSTREAM_URL


def test_missing_key_is_kept_on_the_same_endpoint_and_cleared_on_a_move():
    nbi = {
        "chat_model": section(JETSTREAM_URL + "/", api_key="js-key"),
        "inline_completion_model": section(NEURODESK_URL, api_key="nd-key"),
    }

    updated = mirror.apply_selection(nbi, select(opencode()))

    assert props(updated, "chat_model")["api_key"] == "js-key"
    assert props(updated, "inline_completion_model")["api_key"] == ""


def test_broken_property_lists_are_replaced():
    nbi = {"chat_model": {"provider": "openai-compatible", "properties": "x"}}

    updated = mirror.apply_selection(nbi, select(opencode()))

    assert updated["chat_model"]["properties"] == [
        {"id": "base_url", "value": JETSTREAM_URL},
        {"id": "model_id", "value": "gpt-oss-120b"},
        {"id": "api_key", "value": ""},
    ]


# --- CLI ---------------------------------------------------------------------


def run_cli(tmp_path, nbi, oc, env=None):
    nbi_path = tmp_path / "config.json"
    oc_path = tmp_path / "opencode.json"
    for path, data in ((nbi_path, nbi), (oc_path, oc)):
        if data is not None:
            path.write_bytes(
                data if isinstance(data, bytes) else json.dumps(data).encode()
            )
    base_env = {
        k: v for k, v in os.environ.items() if k != "NEURODESK_API_KEY_VALUE"
    }
    result = subprocess.run(
        [sys.executable, str(HELPER_PATH), str(nbi_path), str(oc_path)],
        capture_output=True,
        text=True,
        env={**base_env, **(env or {})},
        # A permissive umask must not widen the 0600 file.
        umask=0o022,
        timeout=30,
    )
    return result, nbi_path


def test_cli_writes_a_private_file_and_reports_on_stderr_only(tmp_path):
    nbi = {"chat_model": section(NEURODESK_URL)}
    result, path = run_cli(
        tmp_path,
        nbi,
        opencode("neurodesk/model-alpha"),
        {"NEURODESK_API_KEY_VALUE": "secret-nd-key"},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == (
        "nbi_setup.sh: synced Notebook Intelligence to OpenCode selection "
        "neurodesk/model-alpha\n"
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_text().endswith("}\n")
    assert props(json.loads(path.read_text()), "chat_model")["api_key"] == "secret-nd-key"
    assert not (tmp_path / "config.json.tmp").exists()

    again, _ = run_cli(tmp_path, None, None, {"NEURODESK_API_KEY_VALUE": "secret-nd-key"})
    assert (again.returncode, again.stdout, again.stderr) == (0, "", "")


def test_cli_replaces_a_stale_temp_file(tmp_path):
    stale = tmp_path / "config.json.tmp"
    stale.write_text("stale")
    stale.chmod(0o644)

    result, path = run_cli(tmp_path, {"chat_model": section(NEURODESK_URL)}, opencode())

    assert result.returncode == 0
    assert not stale.exists()
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("nbi", "oc"),
    [
        (b'{"chat_model": {"api_key": "do-not-echo"', opencode()),
        (b"\xff\xfe not utf-8", opencode()),
        (b"[]", opencode()),
        (None, opencode()),
        ({"chat_model": section(NEURODESK_URL, api_key="do-not-echo")}, b"{bad"),
        ({"chat_model": section(NEURODESK_URL, api_key="do-not-echo")}, None),
        ({"chat_model": section(NEURODESK_URL, api_key="do-not-echo")}, b"[" * 100000),
    ],
    ids=[
        "corrupt-nbi", "non-utf8-nbi", "nbi-not-object", "missing-nbi",
        "corrupt-opencode", "missing-opencode", "deeply-nested-opencode",
    ],
)
def test_cli_skips_unreadable_configs_silently(tmp_path, nbi, oc):
    result, path = run_cli(tmp_path, nbi, oc)
    before = None if nbi is None else (
        nbi if isinstance(nbi, bytes) else json.dumps(nbi).encode()
    )

    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
    if before is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_cli_write_failure_is_one_line_and_leaves_the_config(tmp_path):
    nbi_dir = tmp_path / "nbi"
    nbi_dir.mkdir()
    nbi = {"chat_model": section(NEURODESK_URL, api_key="do-not-echo")}
    oc = opencode("neurodesk/model-alpha")
    (tmp_path / "opencode.json").write_text(json.dumps(oc))
    path = nbi_dir / "config.json"
    path.write_text(json.dumps(nbi))
    nbi_dir.chmod(0o500)
    try:
        result = subprocess.run(
            [sys.executable, str(HELPER_PATH), str(path), str(tmp_path / "opencode.json")],
            capture_output=True,
            text=True,
            env={**os.environ, "NEURODESK_API_KEY_VALUE": "secret-nd-key"},
            timeout=30,
        )
    finally:
        nbi_dir.chmod(0o700)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith(f"nbi_setup.sh: cannot write {path}: ")
    assert result.stderr.count("\n") == 1
    assert "secret-nd-key" not in result.stderr
    assert "do-not-echo" not in result.stderr
    assert json.loads(path.read_text()) == nbi
    assert not (nbi_dir / "config.json.tmp").exists()


def test_cli_rejects_missing_arguments(tmp_path):
    result = subprocess.run(
        [sys.executable, str(HELPER_PATH), str(tmp_path / "config.json")],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert result.stdout == ""


# --- Packaging ---------------------------------------------------------------


def test_dockerfile_installs_the_helper_beside_nbi_setup():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    wrapper = repo_path("config/agents/nbi_setup.sh").read_text(encoding="utf-8")

    assert (
        "--mount=type=bind,source=config/agents/nbi_mirror_opencode.py,"
        "target=/tmp/agents/nbi_mirror_opencode.py,ro"
    ) in dockerfile
    assert f"install -m 0755 /tmp/agents/nbi_mirror_opencode.py {INSTALLED_PATH}" in (
        dockerfile
    )
    assert "install -m 0755 /tmp/agents/nbi_setup.sh /opt/neurodesktop/nbi_setup.sh" in (
        dockerfile
    )
    assert (
        'NBI_MIRROR_OPENCODE_HELPER="${NBI_SETUP_DIR}/nbi_mirror_opencode.py"'
        in wrapper
    )
    assert re.search(r'python3 "\$\{NBI_MIRROR_OPENCODE_HELPER\}"', wrapper)


def test_nbi_setup_keeps_no_inline_copy_of_the_mirror():
    wrapper = repo_path("config/agents/nbi_setup.sh").read_text(encoding="utf-8")

    for marker in ("def resolve_api_key", "managed_urls", "def set_prop"):
        assert marker not in wrapper


def test_helper_is_in_the_formatting_and_strict_typing_scopes():
    relative = "config/agents/nbi_mirror_opencode.py"

    assert relative in repo_path("scripts/check_quality.py").read_text("utf-8")
    assert relative in repo_path("mypy.ini").read_text("utf-8")
    assert os.access(Path(mirror.SOURCE_PATH), os.X_OK)
