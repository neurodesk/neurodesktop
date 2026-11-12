"""Run the neurodesk-cite command against a recorded usage log and a fixture catalogue."""

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from testlib import repo_path

CLI = repo_path("config/jupyter/neurodesk_cite.py")
CATALOGUE = repo_path("tests/fixtures/neurodesk-cite/apps.json")
FSL_REFERENCE = "1. Woolrich MW et al. Bayesian analysis of neuroimaging data in FSL."
RENTON = "Renton, A.I., Dao, T.T., Johnstone, T., Civier, O., Sullivan, R. P., White, D. J., ..."


class Cite:
    def __init__(self, home: Path):
        self.home = home
        self.log = home / ".local/state/neurodesk/module-usage.tsv"
        self.output = home / "out/CITATIONS.md"
        self.cache = home / ".cache/neurodesk/apps.json"
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


def test_arm64_modules_resolve_by_their_image_name(cite):
    cite.record(
        "ants\t2.6.5\t2026-11-01T09:00:00Z",
        "ants_arm64\t2.6.5\t2026-11-01T10:00:00Z",
        "bidscoin_4.6.2\tarm64\t2026-11-01T11:00:00Z",
    )

    result = cite.run()

    assert result.returncode == 0, result.stderr
    text = cite.text()
    assert "ants (v2.6.5, built 2026-10-12) was run in Neurodesk" in text
    assert "ants_arm64 (v2.6.5, built 2026-10-14) was run in Neurodesk" in text
    assert "bidscoin_4.6.2 (varm64, built 2026-05-12) was run in Neurodesk" in text
    pointer = next(line for line in text.splitlines() if line.startswith("bidscoin_4.6.2:"))
    assert pointer.endswith("/recipes/bidscoin")
    assert text.split("## References\n\n")[1].count("Avants BB et al.") == 1
    assert "not in the Neurodesk catalogue" not in result.stderr


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


class BotFilteredCatalogue(BaseHTTPRequestHandler):
    """Serves the fixture like the catalogue host, which rejects urllib's default agent."""

    def do_GET(self):
        if self.headers.get("User-Agent", "").startswith("Python-urllib"):
            self.send_error(403)
            return
        body = CATALOGUE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_fetches_over_http_past_a_bot_filter(cite):
    server = ThreadingHTTPServer(("127.0.0.1", 0), BotFilteredCatalogue)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cite.catalogue_url = f"http://127.0.0.1:{server.server_port}/neuroimaging/apps.json"
    cite.record("ants\t2.6.5\t2026-11-01T09:00:00Z")
    try:
        result = cite.run()
    finally:
        server.shutdown()

    assert result.returncode == 0, result.stderr
    assert "Could not fetch" not in result.stderr
    assert "ants (v2.6.5, built 2026-10-12)" in cite.text()
    assert cite.cache.is_file()


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


def test_offline_run_ignores_a_cached_applist(cite):
    cite.record("ants\t2.6.5\t2026-11-01T09:00:00Z")
    legacy = cite.cache.with_name("applist.json")
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"list": [{"application": "ants_2.6.5_20261012", "citation": "Avants BB"}]}')
    cite.catalogue_url = (cite.home / "missing.json").as_uri()

    result = cite.run()

    assert result.returncode == 0, result.stderr
    assert "no cached catalogue exists" in result.stderr
    assert "ants (v2.6.5) was run in Neurodesk" in cite.text()


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
