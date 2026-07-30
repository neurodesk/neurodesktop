"""Runtime contract for the MySTRA report CLI.

The unit tier covers scaffolding, which is pure file authoring. Only a built
image can answer the question that matters here: does a scaffolded report
actually render with the pinned plugin and a local theme, without reaching the
network for either?

That offline property is the whole point of building MySTRA and both ASTRA
themes into the image, and it fails silently -- MyST falls back to downloading
a named theme -- so it is asserted against a real build rather than inferred
from the files on disk.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from testlib import run_cmd


CLI = Path("/usr/local/bin/neurodesktop-astra-report")
PILOT_SPEC = Path(
    "/opt/neurodesktop/pilots/astra-lightcone-bet/project/astra.yaml"
)
BUILD_TIMEOUT = 900


def _project(tmp_path, report_text=None):
    """A real ASTRA spec in a writable directory, optionally pre-authored."""
    root = tmp_path / "report-project"
    root.mkdir()
    shutil.copy(PILOT_SPEC, root / "astra.yaml")
    if report_text is not None:
        (root / "index.md").write_text(report_text, encoding="utf-8")
    return root


def _build(root):
    return subprocess.run(
        [str(CLI), "build", "--project-dir", str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=BUILD_TIMEOUT,
        check=False,
    )


def test_the_cli_is_installed_and_executable():
    assert CLI.is_file()
    code, output = run_cmd(f"{CLI} --help", timeout=60)

    assert code == 0, output
    for command in ("scaffold", "build", "chat-block"):
        assert command in output, command


def test_scaffolding_writes_the_paths_that_exist_in_this_image(tmp_path):
    """A scaffold naming a path the image does not ship is a broken build."""
    root = _project(tmp_path)

    code, output = run_cmd(f"{CLI} scaffold --project-dir {root}", timeout=120)
    assert code == 0, output

    config = (root / "myst.yml").read_text(encoding="utf-8")
    for line in config.splitlines():
        candidate = line.split(": ")[-1].strip("- ").strip()
        if candidate.startswith("/opt/neurodesktop/"):
            assert Path(candidate).exists(), candidate
    assert "/opt/neurodesktop/mystra/mystra.mjs" in config
    assert "/opt/neurodesktop/astra-theme/article" in config


def test_the_report_toolchain_builds_offline_with_the_pinned_plugin(tmp_path):
    """The wiring contract, independent of how any embed resolves."""
    root = _project(tmp_path, report_text="# Toolchain check\n\nProse only.\n")

    result = _build(root)

    assert result.returncode == 0, result.stdout
    assert (root / "_build/html/index.html").is_file()
    # A downloaded theme is the failure this whole pinning exists to prevent.
    assert "downloading" not in result.stdout.lower()


def test_the_scaffolded_report_renders_the_analysis(tmp_path):
    """The path an agent actually takes: scaffold, then build, unedited."""
    root = _project(tmp_path)

    result = _build(root)

    assert result.returncode == 0, result.stdout
    rendered = (root / "_build/html/index.html").read_text(
        encoding="utf-8", errors="ignore"
    )
    # The spec's own prose has to reach the page; an embed that silently
    # resolved to nothing would still produce a valid but empty report.
    assert "BET fractional threshold" in rendered


def test_the_chat_block_links_absolute_paths_and_the_real_spec_summary(tmp_path):
    """Only an absolute path inside the server root is claimed by the frontend.

    The summary is the other half: ``astra`` is on PATH here, so this is where
    the relay is exercised against the released CLI rather than a fake.
    """
    root = _project(tmp_path)

    code, output = run_cmd(f"{CLI} chat-block --project-dir {root}", timeout=120)

    assert code == 0, output
    assert f"({root}/astra.yaml)" in output
    assert f"({root}/index.md)" in output
    assert "```" in output, output


def test_the_report_path_is_not_a_second_reader_of_the_astra_schema(tmp_path):
    """`adapter.py` and the released CLI are the only schema-aware readers."""
    source = CLI.read_text(encoding="utf-8")

    assert '["astra", "info"]' in source
    assert "import yaml" not in source
    assert "safe_load" not in source


@pytest.mark.parametrize("theme", ["article", "book"])
def test_both_report_flavors_build_from_the_local_templates(tmp_path, theme):
    (tmp_path / theme).mkdir()
    root = _project(tmp_path / theme, report_text="# Flavor check\n\nProse.\n")

    result = subprocess.run(
        [str(CLI), "build", "--project-dir", str(root), "--theme", theme],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=BUILD_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert f"/opt/neurodesktop/astra-theme/{theme}" in (
        root / "myst.yml"
    ).read_text(encoding="utf-8")
    assert (root / "_build/html/index.html").is_file()
