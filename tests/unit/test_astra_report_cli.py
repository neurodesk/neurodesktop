"""Contract for the MySTRA report CLI.

Scaffolding is pure file authoring, so everything except the real ``myst build``
is exercised here against a temporary project. The build itself needs the pinned
plugin, both themes, and the ``myst`` CLI, so it lives in
``tests/container/test_astra_report_image.py``.

Two properties matter most and are asserted from several angles: an authored
file is never rewritten, and the chat block emits absolute workspace paths --
those are what the click interceptor claims and opens in the main panel.
"""

import importlib.util
import subprocess
import sys

import pytest

from testlib import resolve_source


def _cli_path():
    return resolve_source(
        "/usr/local/bin/neurodesktop-astra-report",
        "scripts/neurodesktop_astra_report.py",
    )


def _load_report_module():
    path = _cli_path()
    spec = importlib.util.spec_from_file_location("neurodesktop_astra_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(_cli_path()), *map(str, args)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


@pytest.fixture
def project(tmp_path):
    """A minimal ASTRA project: the CLI only requires the spec to be present."""
    root = tmp_path / "bet-sensitivity"
    root.mkdir()
    (root / "astra.yaml").write_text('version: "0.0.12"\nname: "Pilot"\n')
    return root


def test_a_directory_without_a_spec_is_not_an_astra_project(tmp_path):
    report = _load_report_module()

    with pytest.raises(report.ReportError, match="astra.yaml"):
        report.project_root(tmp_path)


def test_a_missing_directory_is_reported_rather_than_created(tmp_path):
    report = _load_report_module()

    with pytest.raises(report.ReportError):
        report.project_root(tmp_path / "absent")


def test_scaffold_opts_into_the_pinned_plugin_and_a_local_theme(project):
    """A downloaded theme would break the image's offline contract."""
    report = _load_report_module()

    created = report.scaffold(report.project_root(project), "article")

    assert sorted(created) == ["index.md", "myst.yml"]
    config = (project / "myst.yml").read_text()
    assert "- /opt/neurodesktop/mystra/mystra.mjs" in config
    assert "template: /opt/neurodesktop/astra-theme/article" in config
    assert "- file: index.md" in config
    # A bare template name is how MyST is told to fetch a theme from the network.
    assert "template: book-theme" not in config


def test_both_report_flavors_are_selectable(project):
    report = _load_report_module()

    report.scaffold(report.project_root(project), "book")

    assert "template: /opt/neurodesktop/astra-theme/book" in (
        project / "myst.yml"
    ).read_text()


def test_an_unknown_theme_is_refused(project):
    report = _load_report_module()

    with pytest.raises(report.ReportError, match="unknown theme"):
        report.myst_config("book-theme")


def test_authored_files_survive_a_later_scaffold(project):
    """The scaffold is a starting point, not a template the tool re-applies."""
    report = _load_report_module()
    root = report.project_root(project)
    report.scaffold(root, "article")
    (project / "index.md").write_text("# Hand-written report\n")

    created = report.scaffold(root, "article")

    assert created == []
    assert (project / "index.md").read_text() == "# Hand-written report\n"


def test_a_project_that_already_has_a_config_keeps_it(project):
    report = _load_report_module()
    (project / "myst.yml").write_text("version: 1\nproject: {}\n")

    created = report.scaffold(report.project_root(project), "article")

    assert created == ["index.md"]
    assert (project / "myst.yml").read_text() == "version: 1\nproject: {}\n"


def test_the_starter_report_embeds_collections_rather_than_fixed_ids(project):
    """Collection embeds stay valid as the spec grows; listed ids rot."""
    report = _load_report_module()

    report.scaffold(report.project_root(project), "article")

    text = (project / "index.md").read_text()
    for collection in ("inputs", "decisions", "outputs", "findings"):
        assert f":::{{astra}} {collection}\n:::" in text, collection
    assert text.startswith("# bet-sensitivity\n")


def test_build_refuses_before_invoking_myst_when_assets_are_missing(
    tmp_path, monkeypatch
):
    """A missing plugin must not surface as an opaque MyST failure."""
    report = _load_report_module()
    monkeypatch.setattr(report, "MYSTRA_PLUGIN", tmp_path / "mystra.mjs")
    monkeypatch.setattr(report, "ASTRA_THEME_ROOT", tmp_path / "astra-theme")

    with pytest.raises(report.ReportError, match="not installed") as failure:
        report._require_build_assets()

    assert "mystra.mjs" in str(failure.value)
    assert "astra-theme/article" in str(failure.value)


def test_the_build_pins_the_theme_server_to_ipv4_loopback(monkeypatch):
    """The export crawl connects to 127.0.0.1; the theme must bind there.

    `localhost` resolves to `::1` first in the image, so the theme would bind
    IPv6 only and every fetch from MyST's own crawler is refused.
    """
    report = _load_report_module()
    monkeypatch.delenv("HOST", raising=False)

    assert report._build_env()["HOST"] == "127.0.0.1"


def test_an_explicit_host_still_wins(monkeypatch):
    report = _load_report_module()
    monkeypatch.setenv("HOST", "0.0.0.0")

    assert report._build_env()["HOST"] == "0.0.0.0"


def test_chat_block_links_absolute_workspace_paths(project, monkeypatch):
    report = _load_report_module()
    monkeypatch.setattr(report, "_astra_info", lambda _root: None)

    block = report.chat_block(report.project_root(project))

    assert f"({project}/index.md)" in block
    assert f"({project}/astra.yaml)" in block
    # Relative links would resolve against the chat document, not the workspace.
    assert "(index.md)" not in block
    assert "(./" not in block


def test_chat_block_says_so_when_no_report_has_been_built(project, monkeypatch):
    report = _load_report_module()
    monkeypatch.setattr(report, "_astra_info", lambda _root: None)

    block = report.chat_block(report.project_root(project))

    assert "not built yet" in block
    assert "_build/html/index.html)" not in block


def test_chat_block_links_the_rendered_report_once_it_exists(project, monkeypatch):
    report = _load_report_module()
    monkeypatch.setattr(report, "_astra_info", lambda _root: None)
    built = project / "_build/html/index.html"
    built.parent.mkdir(parents=True)
    built.write_text("<html></html>")

    block = report.chat_block(report.project_root(project))

    assert f"({project}/_build/html/index.html)" in block
    assert "not built yet" not in block


def test_link_targets_are_percent_encoded(tmp_path):
    """The interceptor decodes url.pathname; an unencoded space truncates it."""
    report = _load_report_module()

    path = tmp_path / "my project (v2)" / "index.md"
    link = report._markdown_link(path)

    label, target = link.split("](")
    # The label stays human-readable; only the target is encoded.
    assert label == f"[{path}"
    target = target.rstrip(")")
    assert " " not in target
    assert "%20" in target and "%28" in target and "%29" in target


def test_the_spec_summary_is_relayed_and_capped(project, monkeypatch):
    """The CLI is the only schema reader; this must stay a verbatim relay."""
    report = _load_report_module()
    long_output = "\n".join(f"line {index}" for index in range(200))
    monkeypatch.setattr(report.shutil, "which", lambda _name: "/opt/conda/bin/astra")
    monkeypatch.setattr(
        report.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout=long_output
        ),
    )

    summary = report._astra_info(project)

    assert summary.startswith("line 0\n")
    assert len(summary.splitlines()) == report.INFO_LINE_CAP + 1
    assert summary.endswith("(truncated; run `astra info` for all)")


def test_a_failing_or_absent_astra_leaves_the_chat_block_useful(project, monkeypatch):
    report = _load_report_module()
    monkeypatch.setattr(report.shutil, "which", lambda _name: None)

    block = report.chat_block(report.project_root(project))

    assert report._astra_info(project) is None
    assert f"({project}/astra.yaml)" in block
    assert "```" not in block


def test_the_cli_scaffolds_from_the_working_directory_by_default(project):
    result = subprocess.run(
        [sys.executable, str(_cli_path()), "scaffold"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert (project / "myst.yml").is_file()
    assert (project / "index.md").is_file()


def test_the_cli_fails_clearly_outside_an_astra_project(tmp_path):
    result = _run_cli("scaffold", "--project-dir", tmp_path)

    assert result.returncode == 2
    assert "astra.yaml" in result.stdout


def test_every_subcommand_is_reachable():
    result = _run_cli("--help")

    assert result.returncode == 0
    for command in ("scaffold", "build", "chat-block"):
        assert command in result.stdout, command
