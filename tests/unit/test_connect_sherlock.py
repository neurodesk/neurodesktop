"""Regression tests for the Sherlock Neurodesktop launcher."""

from __future__ import annotations

import os
import shlex
import subprocess

from testlib import repo_path


SCRIPT = repo_path("scripts/connectSherlock.sh")


def run_bash(command: str, *, env: dict[str, str] | None = None):
    return subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_connect_sherlock_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", SCRIPT], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr


def test_connect_sherlock_keeps_image_server_extensions_enabled():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "--ServerApp.jpserver_extensions" not in source
    assert "NEURODESKTOP_DISABLE_JPSERVER_EXTENSIONS" not in source
    assert "'jupyter_server_fileid': False" not in source


def test_connect_sherlock_updates_from_neurodesktop_repository():
    source = SCRIPT.read_text(encoding="utf-8")

    assert (
        "https://raw.githubusercontent.com/neurodesk/neurodesktop/"
        "refs/heads/main/scripts/connectSherlock.sh"
    ) in source


def test_self_update_replaces_and_reexecutes_an_old_copy(tmp_path):
    old_script = tmp_path / "connectSherlock.sh"
    old_script.write_text("#!/bin/bash\necho old copy\n", encoding="utf-8")

    candidate = tmp_path / "candidate.sh"
    candidate.write_text(
        """#!/bin/bash
function connectSherlock() {
    printf 'updated copy ran with %s\\n' "$1"
}
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    connectSherlock "$@"
fi
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["CONNECT_SHERLOCK_TEST_CANDIDATE"] = str(candidate)
    command = f"""
source {shlex.quote(str(SCRIPT))}
download_connect_sherlock_update() {{ cp "$CONNECT_SHERLOCK_TEST_CANDIDATE" "$1"; }}
self_update_connect_sherlock {shlex.quote(str(old_script))} sentinel
"""
    result = run_bash(command, env=env)

    assert result.returncode == 0, result.stderr
    assert "Updated connectSherlock.sh" in result.stdout
    assert "updated copy ran with sentinel" in result.stdout
    assert old_script.read_text(encoding="utf-8") == candidate.read_text(
        encoding="utf-8"
    )


def test_update_validation_rejects_invalid_bash(tmp_path):
    candidate = tmp_path / "candidate.sh"
    candidate.write_text(
        "#!/bin/bash\nfunction connectSherlock() {\n", encoding="utf-8"
    )
    command = (
        f"source {shlex.quote(str(SCRIPT))}; "
        f"validate_connect_sherlock_update {shlex.quote(str(candidate))}"
    )

    result = run_bash(command)

    assert result.returncode != 0
