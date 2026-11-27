import json
import os
import subprocess
from pathlib import Path

import pytest

from testlib import load_source_module


def load_update_page_config_module():
    return load_source_module(
        "update_page_config",
        "/opt/neurodesktop/update_page_config.py",
        "config/jupyter/update_page_config.py",
    )


def test_page_config_no_supporter(tmp_path):
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
    assert "@jupyterhub/jupyter-server-proxy" not in written_payload["disabledExtensions"]
    for extension_name, expected_state in module.DISABLED_EXTENSIONS.items():
        assert written_payload["disabledExtensions"][extension_name] is expected_state
    assert list(page_config_path.parent.glob("*.tmp")) == []


def test_page_config_supporter_preserves(tmp_path):
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
    assert "@jupyterhub/jupyter-server-proxy" not in payload["disabledExtensions"]
    assert payload[module.SUPPORTER_OPTION] == "true"
    for extension_name, expected_state in module.DISABLED_EXTENSIONS.items():
        assert payload["disabledExtensions"][extension_name] is expected_state


def test_page_config_cleans_stale_server_proxy_disable_and_keeps_explicit_false(tmp_path):
    """Verify legacy user-level server-proxy true is cleaned up while explicit false is kept."""
    module = load_update_page_config_module()
    supporter_flag_path = tmp_path / ".config" / "neurodesk_supporter"

    stale_path = tmp_path / "stale" / "page_config.json"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_text(
        json.dumps(
            {
                "customSetting": "keep-me",
                "disabledExtensions": {
                    "@jupyterhub/jupyter-server-proxy": True,
                    "other.extension": True,
                },
            }
        ),
        encoding="utf-8",
    )
    stale_payload = module.ensure_page_config(stale_path, supporter_flag_path)
    assert "@jupyterhub/jupyter-server-proxy" not in stale_payload["disabledExtensions"]
    assert stale_payload["disabledExtensions"]["other.extension"] is True
    assert stale_payload["customSetting"] == "keep-me"

    explicit_false_path = tmp_path / "explicit" / "page_config.json"
    explicit_false_path.parent.mkdir(parents=True)
    explicit_false_path.write_text(
        json.dumps(
            {
                "disabledExtensions": {
                    "@jupyterhub/jupyter-server-proxy": False,
                },
            }
        ),
        encoding="utf-8",
    )
    explicit_payload = module.ensure_page_config(explicit_false_path, supporter_flag_path)
    assert explicit_payload["disabledExtensions"]["@jupyterhub/jupyter-server-proxy"] is False


def test_page_config_preserves_user_symlink_and_updates_target(tmp_path):
    """Verify atomic writes keep a user-managed symlink intact and update its target."""
    module = load_update_page_config_module()
    target = tmp_path / "dotfiles" / "page_config.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "customSetting": "from-dotfiles",
                "disabledExtensions": {"@jupyterhub/jupyter-server-proxy": True},
            }
        ),
        encoding="utf-8",
    )
    link_path = tmp_path / ".jupyter" / "labconfig" / "page_config.json"
    link_path.parent.mkdir(parents=True)
    link_path.symlink_to(target)

    module.ensure_page_config(link_path, tmp_path / ".config" / "neurodesk_supporter")

    assert link_path.is_symlink()
    assert link_path.resolve() == target.resolve()
    updated = json.loads(target.read_text(encoding="utf-8"))
    assert updated["customSetting"] == "from-dotfiles"
    assert "@jupyterhub/jupyter-server-proxy" not in updated["disabledExtensions"]


def test_page_config_unreadable_file_is_not_overwritten(tmp_path):
    """Verify a permission error on read does not erase an existing page_config.json."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses file mode 000 read checks")
    module = load_update_page_config_module()
    page_config_path = tmp_path / ".jupyter" / "labconfig" / "page_config.json"
    page_config_path.parent.mkdir(parents=True)
    original = '{"customSetting": "secret"}\n'
    page_config_path.write_text(original, encoding="utf-8")
    page_config_path.chmod(0o000)
    try:
        with pytest.raises(OSError):
            module.ensure_page_config(page_config_path, tmp_path / ".config" / "neurodesk_supporter")
        page_config_path.chmod(0o600)
        assert page_config_path.read_text(encoding="utf-8") == original
    finally:
        page_config_path.chmod(0o600)


def test_restore_defaults_plus_migration_preserves_user_page_config(tmp_path):
    """Verify restore_home_defaults.sh does not clobber existing page_config.json before migration."""
    repo_root = Path(__file__).resolve().parents[2]
    restore_script = repo_root / "config/jupyter/restore_home_defaults.sh"
    seed_src = repo_root / "config/jupyter/page_config.json"
    if not restore_script.exists() or not seed_src.exists():
        pytest.skip("repo source scripts not available")

    defaults_dir = tmp_path / "defaults"
    seed_dest = defaults_dir / ".jupyter" / "labconfig" / "page_config.json"
    seed_dest.parent.mkdir(parents=True)
    seed_dest.write_text(seed_src.read_text(encoding="utf-8"), encoding="utf-8")

    home_dir = tmp_path / "home"
    user_cfg = home_dir / ".jupyter" / "labconfig" / "page_config.json"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text(
        json.dumps(
            {
                "customSetting": "keep-me",
                "disabledExtensions": {"@jupyterhub/jupyter-server-proxy": False},
            }
        ),
        encoding="utf-8",
    )
    # Make the image seed strictly newer than the user's file.
    os.utime(user_cfg, (1_700_000_000, 1_700_000_000))
    os.utime(seed_dest, (1_800_000_000, 1_800_000_000))

    # Run restore_home_defaults.sh with DEFAULTS_DIR and HOME_DIR pointed at tmp_path.
    patched = restore_script.read_text(encoding="utf-8").replace(
        'DEFAULTS_DIR="/opt/jovyan_defaults"', f'DEFAULTS_DIR="{defaults_dir}"'
    )
    runner = tmp_path / "restore.sh"
    runner.write_text(patched, encoding="utf-8")
    env = dict(os.environ, HOME=str(home_dir), NB_USER="jovyan")
    subprocess.run(["bash", str(runner)], env=env, check=True)

    module = load_update_page_config_module()
    module.ensure_page_config(user_cfg, home_dir / ".config" / "neurodesk_supporter")

    final = json.loads(user_cfg.read_text(encoding="utf-8"))
    assert final["customSetting"] == "keep-me"
    assert final["disabledExtensions"]["@jupyterhub/jupyter-server-proxy"] is False


def test_default_user_page_config_does_not_disable_server_proxy():
    """Verify /opt/jovyan_defaults seed config does not disable server-proxy in user home."""
    repo_default = Path(__file__).resolve().parents[2] / "config/jupyter/page_config.json"
    if not repo_default.exists():
        repo_default = Path("/opt/jovyan_defaults/.jupyter/labconfig/page_config.json")
    data = json.loads(repo_default.read_text(encoding="utf-8"))
    assert "@jupyterhub/jupyter-server-proxy" not in data.get("disabledExtensions", {})
