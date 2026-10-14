import json
import os
import subprocess

import pytest

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


@pytest.mark.parametrize("override, expected", [
    (None, "/opt/jovyan_defaults/.jupyter/nbi/tour_config.json"),
    ("", "/opt/jovyan_defaults/.jupyter/nbi/tour_config.json"),
    ("/tmp/custom-tour.json", "/tmp/custom-tour.json"),
])
def test_nbi_tour_config_default_and_override_are_exported(tmp_path, override, expected):
    env_script = resolve_source(
        "/opt/neurodesktop/environment_variables.sh",
        "config/jupyter/environment_variables.sh",
    )
    environment = {
        "PATH": os.environ["PATH"], "HOME": str(tmp_path),
        "USER": "test-user", "NEURODESKTOP_ENV_SOURCED": "1",
    }
    if override is not None:
        environment["NBI_TOUR_CONFIG_PATH"] = override
    result = subprocess.run(
        ["bash", "-c", 'source "$1" >/dev/null 2>&1 && '
         'bash -c \'printf "%s" "$NBI_TOUR_CONFIG_PATH"\'', "test", str(env_script)],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def test_dockerfile_installs_nbi_tour_config():
    dockerfile = repo_path("Dockerfile")

    assert (
        "install -m 0644 /tmp/agents/nbi_tour_config.json "
        f"{NBI_TOUR_CONFIG_PATH}"
    ) in dockerfile.read_text(encoding="utf-8")
