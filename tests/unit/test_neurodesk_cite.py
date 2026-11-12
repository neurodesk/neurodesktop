"""Run the neurodesk-cite command against a recorded usage log and a fixture catalogue."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from testlib import repo_path

CLI = repo_path("config/jupyter/neurodesk_cite.py")
CATALOGUE = repo_path("tests/fixtures/neurodesk-cite/applist.json")
FSL_REFERENCE = "1. Woolrich MW et al. Bayesian analysis of neuroimaging data in FSL."
RENTON = "Renton, A.I., Dao, T.T., Johnstone, T., Civier, O., Sullivan, R. P., White, D. J., ..."


class Cite:
    def __init__(self, home: Path):
        self.home = home
        self.log = home / ".local/state/neurodesk/module-usage.tsv"
        self.output = home / "out/CITATIONS.md"
        self.cache = home / ".cache/neurodesk/applist.json"
        self.catalogue_url = CATALOGUE.as_uri()

    def record(self, *lines: str) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("a") as stream:
            stream.writelines(line + "\n" for line in lines)

    def run(self, *args: str, output: bool = True) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "NEURODESKTOP_VERSION": "2026-09-23",
            "NEURODESKTOP_CITE_CATALOGUE_URL": self.catalogue_url,
        }
        arguments = [sys.executable, str(CLI), *args]
        if output:
            arguments += ["--output", str(self.output)]
        return subprocess.run(arguments, env=env, capture_output=True, text=True, timeout=60)

    def text(self) -> str:
        return self.output.read_text()


@pytest.fixture
def cite(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return Cite(home)


def test_writes_methods_sentences_and_deduplicated_references(cite):
    cite.record(
        "fsl\t6.0.7.16\t2026-11-01T09:00:00Z",
        "ants\t2.6.5\t2026-11-02T09:00:00Z",
        "fsl\t6.0.7.16\t2026-11-03T09:00:00Z",
        "fsl\t6.0.7.23\t2026-11-04T09:00:00Z",
    )

    result = cite.run()

    assert result.returncode == 0, result.stderr
    text = cite.text()
    methods = text.split("## Methods\n\n")[1].split("\n\n")[0].splitlines()
    assert methods == [
        "fsl (v6.0.7.16, built 2025-01-31) was run in Neurodesk (v2026-09-23, Renton et al., 2024).",
        "ants (v2.6.5, built 2026-10-12) was run in Neurodesk (v2026-09-23, Renton et al., 2024).",
        "fsl (v6.0.7.23, built 2026-10-13) was run in Neurodesk (v2026-09-23, Renton et al., 2024).",
    ]
    references = text.split("## References\n\n")[1]
    assert references.count(FSL_REFERENCE) == 1
    assert "Avants BB et al." in references
    assert references.index("Avants BB") < references.index(RENTON)
    assert "2026-11-01 and 2026-11-04" in text
    assert "AEDAPT" not in text


def test_tools_without_a_citation_point_to_the_guide_and_recipe(cite):
    cite.record("mne_arm64\t1.13.2\t2026-11-01T09:00:00Z", "bidscoin\t4.6.2\t2026-11-01T10:00:00Z")

    result = cite.run()

    assert result.returncode == 0, result.stderr
    text = cite.text()
    assert "mne_arm64 (v1.13.2, built 2026-11-08) was run in Neurodesk" in text
    assert "https://neurodesk.org/overview/how-to-cite-us/" in text
    recipe = "https://github.com/neurodesk/neurocontainers/tree/main/recipes/"
    assert any(line.endswith(recipe + "mne") for line in text.splitlines())
    assert any(line.endswith(recipe + "bidscoin") for line in text.splitlines())


def test_electrophysiology_tools_add_the_aedapt_note(cite):
    cite.record("mne_arm64\t1.13.2\t2026-11-01T09:00:00Z")

    assert cite.run().returncode == 0
    assert "Please also cite the AEDAPT project" in cite.text()


def test_modules_missing_from_the_catalogue_are_cited_without_a_build_date(cite):
    cite.record("mytool\t1.0\t2026-11-01T09:00:00Z")

    result = cite.run()

    assert result.returncode == 0, result.stderr
    assert "mytool (v1.0) was run in Neurodesk (v2026-09-23, Renton et al., 2024)." in cite.text()
    assert "recipes/mytool" in cite.text()
    assert "mytool/1.0 is not in the Neurodesk catalogue" in result.stderr


def test_offline_run_uses_the_cached_catalogue_and_says_so(cite):
    cite.record("ants\t2.6.5\t2026-11-01T09:00:00Z")
    assert cite.run().returncode == 0
    assert cite.cache.is_file()

    cite.catalogue_url = (cite.home / "missing.json").as_uri()
    cite.output.unlink()
    result = cite.run()

    assert result.returncode == 0, result.stderr
    assert "using the catalogue cached on" in result.stderr
    assert "ants (v2.6.5, built 2026-10-12)" in cite.text()
    assert "Avants BB" in cite.text()


def test_offline_run_without_a_cache_still_writes_pointers(cite):
    cite.record("ants\t2.6.5\t2026-11-01T09:00:00Z")
    cite.catalogue_url = (cite.home / "missing.json").as_uri()

    result = cite.run()

    assert result.returncode == 0, result.stderr
    assert "no cached catalogue exists" in result.stderr
    assert "ants (v2.6.5) was run in Neurodesk" in cite.text()
    assert "recipes/ants" in cite.text()
    assert not cite.cache.exists()


def test_since_limits_the_loads_that_are_cited(cite):
    cite.record("fsl\t6.0.7.16\t2026-10-30T23:59:59Z", "ants\t2.6.5\t2026-11-01T00:00:00Z")

    assert cite.run("--since", "2026-11-01").returncode == 0
    assert "ants (v2.6.5" in cite.text()
    assert "fsl" not in cite.text()


def test_malformed_log_lines_are_ignored(cite):
    cite.record("garbage", "fsl\t6.0.7.16\tyesterday", "\t1.0\t2026-11-01T09:00:00Z", "ants\t2.6.5\t2026-11-01T09:00:00Z")

    assert cite.run().returncode == 0
    methods = cite.text().split("## Methods\n\n")[1].split("\n\n")[0]
    assert methods.splitlines() == [
        "ants (v2.6.5, built 2026-10-12) was run in Neurodesk (v2026-09-23, Renton et al., 2024)."
    ]


def test_empty_log_reports_and_writes_nothing(cite):
    result = cite.run()

    assert result.returncode == 1
    assert "No Neurodesk module loads are recorded" in result.stderr
    assert not cite.output.exists()


def test_clear_empties_the_log(cite):
    cite.record("ants\t2.6.5\t2026-11-01T09:00:00Z")

    assert cite.run("--clear", output=False).returncode == 0
    assert cite.log.read_text() == ""
    assert cite.run().returncode == 1


def test_default_output_is_the_storage_folder(cite):
    storage = cite.home / "neurodesktop-storage"
    storage.mkdir()
    cite.record("ants\t2.6.5\t2026-11-01T09:00:00Z")

    result = cite.run(output=False)

    assert result.returncode == 0, result.stderr
    assert "ants (v2.6.5" in (storage / "CITATIONS.md").read_text()
    assert str(storage / "CITATIONS.md") in result.stdout


def test_output_follows_the_umask_like_other_user_files(cite):
    cite.record("ants\t2.6.5\t2026-11-01T09:00:00Z", "ants\t2.6.5\t2026-11-01T10:00:00Z")
    umask = os.umask(0o022)
    try:
        assert cite.run().returncode == 0
    finally:
        os.umask(umask)

    assert cite.output.stat().st_mode & 0o777 == 0o644
    assert "2 recorded module loads on 2026-11-01." in cite.text()
