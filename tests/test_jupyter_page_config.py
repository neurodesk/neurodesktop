import importlib.util
import json
from pathlib import Path

import pytest


def load_update_page_config_module():
    repo_root = Path(__file__).resolve().parents[1]
    candidates = (
        Path("/opt/neurodesktop/update_page_config.py"),
        repo_root / "config/jupyter/update_page_config.py",
    )

    for candidate in candidates:
        if not candidate.exists():
            continue

        spec = importlib.util.spec_from_file_location("update_page_config", candidate)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    pytest.skip("update_page_config.py is not available in this environment")


def test_update_page_config_sets_supporter_false_when_marker_missing(tmp_path):
    """Verify missing supporter marker keeps the donation banner enabled."""
    module = load_update_page_config_module()
    page_config_path = tmp_path / ".jupyter" / "labconfig" / "page_config.json"

    page_config_path.parent.mkdir(parents=True)
    page_config_path.write_text("{invalid json", encoding="utf-8")

    payload = module.ensure_page_config(
        page_config_path,
        tmp_path / ".config" / "neurodesk_supporter",
    )

    written_payload = json.loads(page_config_path.read_text(encoding="utf-8"))

    assert written_payload == payload
    assert written_payload[module.SUPPORTER_OPTION] == "false"
    for extension_name, expected_state in module.DISABLED_EXTENSIONS.items():
        assert written_payload["disabledExtensions"][extension_name] is expected_state


def test_update_page_config_preserves_existing_settings_and_sets_supporter_true(tmp_path):
    """Verify existing config survives while the supporter marker suppresses the banner."""
    module = load_update_page_config_module()
    page_config_path = tmp_path / ".jupyter" / "labconfig" / "page_config.json"
    supporter_flag_path = tmp_path / ".config" / "neurodesk_supporter"

    supporter_flag_path.parent.mkdir(parents=True)
    supporter_flag_path.write_text("", encoding="utf-8")

    page_config_path.parent.mkdir(parents=True)
    page_config_path.write_text(
        json.dumps(
            {
                "customSetting": "keep-me",
                "disabledExtensions": {
                    "example.extension": False,
                },
            }
        ),
        encoding="utf-8",
    )

    payload = module.ensure_page_config(page_config_path, supporter_flag_path)

    assert payload["customSetting"] == "keep-me"
    assert payload["disabledExtensions"]["example.extension"] is False
    assert payload[module.SUPPORTER_OPTION] == "true"
    for extension_name, expected_state in module.DISABLED_EXTENSIONS.items():
        assert payload["disabledExtensions"][extension_name] is expected_state
