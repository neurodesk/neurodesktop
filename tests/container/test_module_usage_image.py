"""The installed Lmod site package records loads that neurodesk-cite turns into citations."""

import json
import os
import subprocess


def test_installed_hook_and_command_cite_a_loaded_module(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    store = tmp_path / "containers"
    modulefile = store / "modules" / "citetool" / "1.2.lua"
    modulefile.parent.mkdir(parents=True)
    modulefile.write_text('setenv("CITETOOL_LOADED", "yes")\n')
    catalogue = tmp_path / "applist.json"
    catalogue.write_text(json.dumps({"list": [{
        "application": "citetool_1.2_20260101",
        "categories": ["electrophysiology"],
        "citation": "Doe J. Citetool. J Tests (2026).",
    }]}))
    assert os.environ["LMOD_PACKAGE_PATH"] == "/opt/neurodesktop/lmod"
    env = {
        **os.environ,
        "HOME": str(home),
        "NEURODESKTOP_LOCAL_CONTAINERS": str(store),
        "MODULEPATH": str(store / "modules"),
        "NEURODESKTOP_CITE_CATALOGUE_URL": catalogue.as_uri(),
    }
    env.pop("XDG_STATE_HOME", None)
    env.pop("XDG_CACHE_HOME", None)

    loaded = subprocess.run(
        ["bash", "-c", 'source /usr/share/lmod/lmod/init/bash; module load citetool/1.2 && echo "$CITETOOL_LOADED"'],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert loaded.stdout.strip() == "yes", loaded.stderr

    output = tmp_path / "CITATIONS.md"
    cited = subprocess.run(
        ["neurodesk-cite", "--output", str(output)], env=env, capture_output=True, text=True, timeout=60,
    )
    assert cited.returncode == 0, cited.stderr
    text = output.read_text()
    version = os.environ["NEURODESKTOP_VERSION"]
    assert f"citetool (v1.2, built 2026-01-01) was run in Neurodesk (v{version}, Renton et al., 2024)." in text
    assert "Doe J. Citetool. J Tests (2026)." in text
    assert "AEDAPT" in text
