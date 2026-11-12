"""Load modules through real Lmod with the Neurodesk site package installed.

The unit-test workflow installs Ubuntu's lmod package; a checkout without Lmod
skips this module. tests/container/test_module_usage_image.py checks the
installed hook in the image.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from testlib import repo_path

LMOD = Path("/usr/share/lmod/lmod/libexec/lmod")
SITE_PACKAGE_DIR = repo_path("config/lmod")
CLI = repo_path("config/jupyter/neurodesk_cite.py")
CATALOGUE = repo_path("tests/fixtures/neurodesk-cite/applist.json")

pytestmark = pytest.mark.skipif(not LMOD.exists(), reason="Lmod is not installed")


@pytest.fixture
def site(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    store = tmp_path / "containers"
    neurodesk_modules = store / "modules"
    other_modules = tmp_path / "other-modules"
    for root, name in ((neurodesk_modules, "ants"), (neurodesk_modules, "fsl"), (other_modules, "gcc")):
        modulefile = root / name / "1.0.lua"
        modulefile.parent.mkdir(parents=True)
        modulefile.write_text(f'setenv("LOADED_{name.upper()}", "yes")\n')
    (neurodesk_modules / "ants" / "2.6.5.lua").write_text('setenv("LOADED_ANTS", "yes")\n')

    def load(*modules: str) -> str:
        result = subprocess.run(
            [str(LMOD), "bash", "load", *modules],
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "LMOD_PACKAGE_PATH": str(SITE_PACKAGE_DIR),
                "NEURODESKTOP_LOCAL_CONTAINERS": str(store),
                "MODULEPATH": f"{neurodesk_modules}:{other_modules}",
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    load.home = home
    load.log = home / ".local/state/neurodesk/module-usage.tsv"
    return load


def test_records_neurodesk_modules_only(site):
    output = site("ants/2.6.5", "gcc/1.0")

    assert "LOADED_ANTS" in output and "LOADED_GCC" in output
    lines = site.log.read_text().splitlines()
    assert len(lines) == 1
    name, version, stamp = lines[0].split("\t")
    assert (name, version) == ("ants", "2.6.5")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp)


def test_each_load_appends_a_line(site):
    site("ants/2.6.5")
    site("fsl/1.0")

    assert [line.split("\t")[:2] for line in site.log.read_text().splitlines()] == [
        ["ants", "2.6.5"],
        ["fsl", "1.0"],
    ]


def test_unwritable_log_does_not_break_module_load(site):
    (site.home / ".local").write_text("a file where the state directory should be")

    output = site("ants/2.6.5")

    assert "LOADED_ANTS" in output
    assert not site.log.exists()


def test_recorded_loads_feed_neurodesk_cite(site, tmp_path):
    site("ants/2.6.5")
    output = tmp_path / "CITATIONS.md"

    result = subprocess.run(
        [sys.executable, str(CLI), "--output", str(output)],
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(site.home),
            "NEURODESKTOP_VERSION": "2026-09-23",
            "NEURODESKTOP_CITE_CATALOGUE_URL": CATALOGUE.as_uri(),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "ants (v2.6.5, built 2026-10-12) was run in Neurodesk (v2026-09-23" in output.read_text()
