"""Compatibility patch for Lightcone's latest stable ASTRA dependency."""

import pytest

from testlib import load_source_module, repo_path


PYPROJECT = '''dependencies = [
    # Pinned: astra-tools 0.2.14 made `astra init` idempotent, changing
    # the callback signature this release's `lc init` delegates to.
    "astra-tools==0.2.11",
    "click>=8.0",
]
'''

COMMANDS = '''def init(directory, no_git):
    from astra.cli import init as astra_init

    try:
        astra_init.callback(directory=directory, no_git=True)  # type: ignore[misc]
    except SystemExit as e:
        raise RuntimeError from e
'''


def load_patcher_module():
    return load_source_module(
        "lightcone_cli_patch",
        "/opt/neurodesktop/patch_lightcone_cli.py",
        "config/agents/patch_lightcone_cli.py",
    )


def write_upstream_fixture(source_dir):
    commands_dir = source_dir / "src" / "lightcone" / "cli"
    commands_dir.mkdir(parents=True)
    (source_dir / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (commands_dir / "commands.py").write_text(COMMANDS, encoding="utf-8")


def test_patch_updates_dependency_and_callback_and_is_idempotent(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)

    assert patcher.patch_source(tmp_path, "0.2.17")

    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    commands = (
        tmp_path / "src" / "lightcone" / "cli" / "commands.py"
    ).read_text(encoding="utf-8")
    assert '"astra-tools==0.2.17"' in pyproject
    assert "astra-tools==0.2.11" not in pyproject
    assert (
        "astra_init.callback(\n"
        "            directory=directory, no_git=True, check_only=False, as_json=False\n"
        "        )"
    ) in commands
    compile(commands, "commands.py", "exec")

    assert not patcher.patch_source(tmp_path, "0.2.17")


def test_patch_refuses_upstream_drift(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        PYPROJECT.replace("astra-tools==0.2.11", "astra-tools>=0.2.11"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="dependency anchor"):
        patcher.patch_source(tmp_path, "0.2.17")


def test_docker_build_verifies_and_patches_the_stable_sdist():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")
    assert 'ARG LIGHTCONE_CLI_SHA256="f3105f04' in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "source=config/agents/patch_lightcone_cli.py" in dockerfile
    assert "/opt/conda/bin/python /tmp/patch_lightcone_cli.py" in dockerfile
    assert 'uv tool install /tmp/lightcone-cli-src' in dockerfile
    assert '--with "snakemake==${SNAKEMAKE_VERSION}"' in dockerfile
    assert '--with "packaging==25.0"' in dockerfile
    assert "m.version('snakemake') == '${SNAKEMAKE_VERSION}'" in dockerfile
