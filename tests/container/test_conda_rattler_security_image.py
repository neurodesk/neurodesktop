"""Installed-image checks for the py-rattler vulnerability mitigation."""

import json
import subprocess


CONDA = "/opt/conda/bin/conda"
PYTHON = "/opt/conda/bin/python"


def _run(command, timeout=30):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_rattler_native_extension_is_absent():
    packages = json.loads(_run([CONDA, "list", "--json"]).stdout)
    installed = {package["name"] for package in packages}
    result = _run(
        [
            PYTHON,
            "-c",
            "import importlib.util; print(importlib.util.find_spec('rattler'))",
        ]
    )

    assert "conda-rattler-solver" not in installed
    assert "py-rattler" not in installed
    assert result.stdout.strip() == "None"


def test_conda_uses_libmamba_without_the_rattler_plugin():
    info = json.loads(_run([CONDA, "info", "--json"]).stdout)
    solver = _run([CONDA, "config", "--show", "solver"]).stdout

    assert info["solver"]["name"] == "libmamba"
    assert solver.strip() == "solver: libmamba"


def test_libmamba_can_solve_an_environment_from_a_local_channel(tmp_path):
    info = json.loads(_run([CONDA, "info", "--json"]).stdout)
    channel = tmp_path / "channel"
    package = {
        "name": "solver-probe",
        "version": "1",
        "build": "0",
        "build_number": 0,
        "depends": [],
        "subdir": info["platform"],
        "md5": "0" * 32,
        "sha256": "0" * 64,
        "size": 1,
    }
    for subdir in (info["platform"], "noarch"):
        repodata = {
            "info": {"subdir": subdir},
            "packages": (
                {"solver-probe-1-0.tar.bz2": package}
                if subdir == info["platform"]
                else {}
            ),
            "packages.conda": {},
            "removed": [],
            "repodata_version": 1,
        }
        directory = channel / subdir
        directory.mkdir(parents=True)
        (directory / "repodata.json").write_text(json.dumps(repodata))

    # A file-only channel needs no network. --offline would restrict libmamba
    # to cached repodata and hide this newly generated channel.
    result = _run(
        [
            CONDA,
            "create",
            "--dry-run",
            "--json",
            "--no-default-packages",
            "--override-channels",
            "--channel",
            channel.as_uri(),
            "--prefix",
            str(tmp_path / "probe-env"),
            "--solver=libmamba",
            "solver-probe=1",
        ],
        timeout=60,
    )

    plan = json.loads(result.stdout)
    assert plan["success"] is True
    assert [package["name"] for package in plan["actions"]["LINK"]] == [
        "solver-probe"
    ]


def test_vulnerable_py_rattler_versions_are_disallowed():
    config = _run([CONDA, "config", "--show", "disallowed_packages"]).stdout

    assert "py-rattler <0.28.0" in config
