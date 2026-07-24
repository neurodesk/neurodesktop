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

    assert dockerfile.count("jupyterlab_myst==2.6.0") == 1
    assert "npm_config_cache=/tmp/myst-npm-cache npm ci" in myst_build
    assert "npm install" not in myst_build
    assert "pnpm" not in myst_build
