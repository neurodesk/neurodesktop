import json

from testlib import repo_path, resolve_source


NBI_TOUR_CONFIG_PATH = "/opt/jovyan_defaults/.jupyter/nbi/tour_config.json"
KNOWN_TOUR_STEPS = {
    "welcome",
    "new-chat",
    "claude-history",
    "settings-gear",
    "slash-commands",
    "add-context",
    "upload-file",
    "drag-and-drop",
    "chat-mode",
    "launcher-tiles",
    "done",
}


def test_nbi_tour_config_disables_all_known_steps():
    tour_config = resolve_source(
        NBI_TOUR_CONFIG_PATH, "config/agents/nbi_tour_config.json"
    )

    payload = json.loads(tour_config.read_text(encoding="utf-8"))
    steps = payload.get("steps")

    assert set(steps) == KNOWN_TOUR_STEPS
    for step_id, step_config in steps.items():
        assert step_config == {"enabled": False}, step_id


def test_nbi_tour_config_path_is_exported():
    env_script = resolve_source(
        "/opt/neurodesktop/environment_variables.sh",
        "config/jupyter/environment_variables.sh",
    )
    expected_export = (
        f'export NBI_TOUR_CONFIG_PATH="${{NBI_TOUR_CONFIG_PATH:-'
        f'{NBI_TOUR_CONFIG_PATH}}}"'
    )

    assert expected_export in env_script.read_text(encoding="utf-8")


def test_dockerfile_installs_nbi_tour_config():
    dockerfile = repo_path("Dockerfile")

    assert (
        "install -m 0644 /tmp/agents/nbi_tour_config.json "
        f"{NBI_TOUR_CONFIG_PATH}"
    ) in dockerfile.read_text(encoding="utf-8")
