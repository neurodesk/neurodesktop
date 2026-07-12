import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
JUPYTER_TEST_WORKFLOW = REPO_ROOT / ".github/workflows/jupyter_test_main.yml"


def _read_repo_file(path: Path) -> str:
    if path.exists():
        return path.read_text()
    if REPO_ROOT == Path("/opt"):
        pytest.skip("repo-only .github workflow files are not bundled into /opt/tests")
    return path.read_text()


def _fsl_probe_command(workflow: str) -> str:
    for line in workflow.splitlines():
        stripped = line.strip()
        if stripped.startswith('CMD4="') and stripped.endswith('"'):
            return stripped.removeprefix('CMD4="').removesuffix('"')
    raise AssertionError("FSL probe command not found in JupyterHub workflow")


def _fslmaths_probe_command(workflow: str) -> str:
    for line in workflow.splitlines():
        stripped = line.strip()
        if stripped.startswith('CMD5="') and stripped.endswith('"'):
            result = subprocess.run(
                ["bash", "-c", f"{stripped}\nprintf '%s' \"$CMD5\""],
                capture_output=True,
                check=True,
                text=True,
            )
            return result.stdout
    raise AssertionError("FSLMaths probe command not found in JupyterHub workflow")


def test_jupyterhub_fsl_module_load_requires_fslmaths_on_path():
    workflow = _read_repo_file(JUPYTER_TEST_WORKFLOW)

    assert "if [ ${#ML_OUT} -ge 0 ]" not in workflow
    assert (
        "source /opt/neurodesktop/environment_variables.sh >/dev/null 2>&1 "
        "&& ml fsl"
    ) in workflow
    assert "ml fsl && command -v fslmaths" in workflow
    assert "__FSL_MODULE_READY_${attempt}__" in workflow
    assert "echo ${FSL_READY_MARKER}" not in workflow
    assert "echo '__FSL_MODULE_READY_'${attempt}'__'" in workflow
    assert r"""(printf '%s\n' "[\"stdin\", \"$CMD4\\r\\n\"]" && sleep 25)""" in workflow
    assert 'grep -Fq "$FSL_READY_MARKER"' in workflow
    assert "FSL module loaded and fslmaths is on PATH" in workflow


def test_jupyterhub_fsl_probe_emits_marker_only_after_tool_is_found(tmp_path):
    workflow = _read_repo_file(JUPYTER_TEST_WORKFLOW)
    command = _fsl_probe_command(workflow)
    tool = tmp_path / "fslmaths"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    refresh = tmp_path / "environment_variables.sh"
    refresh.write_text(
        f'ml() {{ return 0; }}\nexport PATH="{tmp_path}:$PATH"\n'
    )
    command = command.replace(
        "/opt/neurodesktop/environment_variables.sh", str(refresh)
    )

    result = subprocess.run(
        ["bash", "-c", f"attempt=positive\n{command}"],
        capture_output=True,
        check=False,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 0
    assert str(tool) in result.stdout
    assert "__FSL_MODULE_READY_positive__" in result.stdout


def test_jupyterhub_fsl_probe_rejects_missing_module():
    workflow = _read_repo_file(JUPYTER_TEST_WORKFLOW)
    command = _fsl_probe_command(workflow)
    command = command.replace(
        "/opt/neurodesktop/environment_variables.sh", "/dev/null"
    )
    missing_module_command = command.replace(
        "ml fsl && command -v fslmaths",
        "module load funny-name-tool && command -v funny-name-tool",
    )

    result = subprocess.run(
        [
            "bash",
            "-c",
            f"module() {{ return 1; }}\nattempt=negative\n{missing_module_command}",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "__FSL_MODULE_READY_negative__" not in result.stdout


def test_jupyterhub_fslmaths_test_is_skipped_when_module_load_fails():
    workflow = _read_repo_file(JUPYTER_TEST_WORKFLOW)

    assert "FSL_MODULE_LOADED=false" in workflow
    assert 'if [ "$FSL_MODULE_LOADED" = true ]; then' in workflow
    assert "Skipping FSLMaths command because FSL module loading failed" in workflow


def _write_fake_tool(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def test_jupyterhub_fslmaths_probe_runs_a_real_image_operation(tmp_path):
    workflow = _read_repo_file(JUPYTER_TEST_WORKFLOW)
    command = _fslmaths_probe_command(workflow)
    assert "__FSLMATHS_VALID_OUTPUT__" not in command
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    _write_fake_tool(
        tmp_path / "python",
        'for last; do :; done\nprintf "input" > "$last"',
    )
    _write_fake_tool(
        tmp_path / "fslmaths",
        'for last; do :; done\nprintf "output" > "$last"',
    )

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        check=False,
        text=True,
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "TMPDIR": str(scratch),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "__FSLMATHS_VALID_OUTPUT__" in result.stdout
    assert not list(scratch.iterdir()), "FSL probe left its temporary directory behind"


def test_jupyterhub_fslmaths_probe_rejects_failed_operation(tmp_path):
    workflow = _read_repo_file(JUPYTER_TEST_WORKFLOW)
    command = _fslmaths_probe_command(workflow)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    _write_fake_tool(
        tmp_path / "python",
        'for last; do :; done\nprintf "input" > "$last"',
    )
    _write_fake_tool(tmp_path / "fslmaths", "exit 1")

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        check=False,
        text=True,
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "TMPDIR": str(scratch),
        },
    )

    assert result.returncode != 0
    assert "__FSLMATHS_VALID_OUTPUT__" not in result.stdout
    assert not list(scratch.iterdir()), "failed FSL probe left temporary files behind"


def test_jupyterhub_fslmaths_output_is_captured_from_the_original_websocket():
    workflow = _read_repo_file(JUPYTER_TEST_WORKFLOW)

    assert 'FSL_WEBSOCKET_LOG=$(mktemp)' in workflow
    assert '> "$FSL_WEBSOCKET_LOG" 2>&1 &' in workflow
    assert 'grep -Fq "$FSL_RUN_MARKER"' in workflow
    assert 'grep -q "Usage: fslmaths"' not in workflow


def test_repo_only_workflow_checks_skip_in_baked_image_layout(monkeypatch, tmp_path):
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "REPO_ROOT", Path("/opt"))
    monkeypatch.setattr(
        module,
        "JUPYTER_TEST_WORKFLOW",
        tmp_path / "missing-jupyter-test-workflow.yml",
    )

    for check in (
        test_jupyterhub_fsl_module_load_requires_fslmaths_on_path,
        test_jupyterhub_fslmaths_test_is_skipped_when_module_load_fails,
        test_jupyterhub_fslmaths_output_is_captured_from_the_original_websocket,
    ):
        with pytest.raises(pytest.skip.Exception):
            check()
