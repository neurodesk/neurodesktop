import json
from pathlib import Path


def _dockerfile_path():
    checkout_dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    if checkout_dockerfile.exists():
        return checkout_dockerfile
    packaged_dockerfile = Path("/opt/tests/Dockerfile")
    if packaged_dockerfile.exists():
        return packaged_dockerfile
    raise AssertionError("Dockerfile not found in checkout or packaged image tests")


def test_myst_rise_build_uses_pinned_compatible_release():
    dockerfile = _dockerfile_path().read_text(encoding="utf-8")
    start = dockerfile.index(
        "# Workaround for jupyterlab-rise + jupyterlab-myst incompatibility:"
    )
    end = dockerfile.index("# Patch both nested tar copies", start)
    myst_build = dockerfile[start:end]

    assert dockerfile.count("jupyterlab_myst==2.7.0") == 1
    assert "pnpm@${MYST_PNPM_VERSION} install --frozen-lockfile" in myst_build
    assert 'pnpm@${MYST_PNPM_VERSION} add --save-exact "@jupyter/ydoc@${MYST_YDOC_VERSION}"' in myst_build
    assert 'dependencies.@jupyter/ydoc=^4.0.0' not in myst_build
    assert "npm install" not in myst_build


def test_legacy_mathjax3_frontend_is_not_exposed_to_jupyterlab():
    dockerfile = _dockerfile_path().read_text(encoding="utf-8")
    legacy_extension = (
        "/opt/conda/share/jupyter/labextensions/"
        "@jupyterlab/mathjax3-extension"
    )
    assert f'rm -rf "{legacy_extension}"' in dockerfile

    app_package = Path("/opt/conda/share/jupyter/lab/static/package.json")
    if app_package.exists():
        assert not Path(legacy_extension).exists()
        dependencies = json.loads(app_package.read_text())["dependencies"]
        assert "@jupyterlab/mathjax-extension" in dependencies
