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


def test_jupyterhub_fsl_module_load_requires_fslmaths_on_path():
    workflow = _read_repo_file(JUPYTER_TEST_WORKFLOW)

    assert "if [ ${#ML_OUT} -ge 0 ]" not in workflow
    assert "ml fsl && command -v fslmaths" in workflow
    assert "__FSL_MODULE_READY_${attempt}__" in workflow
    assert "FSL module loaded and fslmaths is on PATH" in workflow


def test_jupyterhub_fslmaths_test_is_skipped_when_module_load_fails():
    workflow = _read_repo_file(JUPYTER_TEST_WORKFLOW)

    assert "FSL_MODULE_LOADED=false" in workflow
    assert 'if [ "$FSL_MODULE_LOADED" = true ]; then' in workflow
    assert "Skipping FSLMaths command because FSL module loading failed" in workflow


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
    ):
        with pytest.raises(pytest.skip.Exception):
            check()
