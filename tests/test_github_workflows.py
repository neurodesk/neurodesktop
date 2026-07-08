from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
JUPYTER_TEST_WORKFLOW = REPO_ROOT / ".github/workflows/jupyter_test_main.yml"


def test_jupyterhub_fsl_module_load_requires_fslmaths_on_path():
    workflow = JUPYTER_TEST_WORKFLOW.read_text()

    assert "if [ ${#ML_OUT} -ge 0 ]" not in workflow
    assert "ml fsl && command -v fslmaths" in workflow
    assert "__FSL_MODULE_READY_${attempt}__" in workflow
    assert "FSL module loaded and fslmaths is on PATH" in workflow


def test_jupyterhub_fslmaths_test_is_skipped_when_module_load_fails():
    workflow = JUPYTER_TEST_WORKFLOW.read_text()

    assert "FSL_MODULE_LOADED=false" in workflow
    assert 'if [ "$FSL_MODULE_LOADED" = true ]; then' in workflow
    assert "Skipping FSLMaths command because FSL module loading failed" in workflow
