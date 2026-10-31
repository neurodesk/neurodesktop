"""Installed-image checks for the py-rattler vulnerability mitigation."""

import hashlib
import io
import json
import subprocess
import tarfile

import pytest


CONDA = "/opt/conda/bin/conda"
PYTHON = "/opt/conda/bin/python"


def _run(command, timeout=30, expected_returncode=0):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == expected_returncode, result.stdout + result.stderr
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


@pytest.mark.parametrize(
    ("name", "version", "blocked"),
    [
        ("solver-probe", "1", False),
        ("py-rattler", "0.27.0", True),
        ("py-rattler", "0.28.0", False),
    ],
)
def test_libmamba_installs_local_packages_and_blocks_vulnerable_versions(
    tmp_path, name, version, blocked
):
    """Install harmless fixture packages and verify Conda's transaction policy."""
    info = json.loads(_run([CONDA, "info", "--json"]).stdout)
    channel = tmp_path / "channel"
    package = {
        "name": name,
        "version": version,
        "build": "0",
        "build_number": 0,
        "depends": [],
        "subdir": info["platform"],
    }
    package_dir = channel / info["platform"]
    package_dir.mkdir(parents=True)
    archive = package_dir / f"{name}-{version}-0.tar.bz2"
    with tarfile.open(archive, "w:bz2") as bundle:
        for filename, content in {
            "info/index.json": json.dumps(package).encode(),
            "info/files": b"probe.txt\n",
            "probe.txt": b"harmless solver fixture\n",
        }.items():
            member = tarfile.TarInfo(filename)
            member.size = len(content)
            member.mode = 0o644
            bundle.addfile(member, io.BytesIO(content))
    package["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    package["size"] = archive.stat().st_size
    for subdir in (info["platform"], "noarch"):
        repodata = {
            "info": {"subdir": subdir},
            "packages": (
                {f"{name}-{version}-0.tar.bz2": package}
                if subdir == info["platform"]
                else {}
            ),
            "packages.conda": {},
            "removed": [],
            "repodata_version": 1,
        }
        directory = channel / subdir
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "repodata.json").write_text(json.dumps(repodata))

    # A file-only channel needs no network. --offline would restrict libmamba
    # to cached repodata and hide this newly generated channel. A real install
    # is required because disallowed_packages is enforced after the solve.
    result = _run(
        [
            CONDA,
            "create",
            "--yes",
            "--json",
            "--no-default-packages",
            "--override-channels",
            "--channel",
            channel.as_uri(),
            "--prefix",
            str(tmp_path / "probe-env"),
            "--solver=libmamba",
            f"{name}={version}",
        ],
        timeout=60,
        expected_returncode=1 if blocked else 0,
    )

    plan = json.loads(result.stdout)
    if blocked:
        assert [error["exception_name"] for error in plan["errors"]] == [
            "DisallowedPackageError"
        ], plan
        assert not (tmp_path / "probe-env/probe.txt").exists()
        return
    assert plan["success"] is True
    assert [(package["name"], package["version"]) for package in plan["actions"]["LINK"]] == [
        (name, version)
    ]
    assert (tmp_path / "probe-env/probe.txt").read_text() == "harmless solver fixture\n"


def test_vulnerable_py_rattler_versions_are_disallowed():
    config = _run([CONDA, "config", "--show", "disallowed_packages"]).stdout

    assert "py-rattler <0.28.0" in config


def test_libmamba_stays_off_the_release_that_breaks_local_channels():
    packages = json.loads(_run([CONDA, "list", "--json", "^libmamba(py)?$"]).stdout)
    config = _run([CONDA, "config", "--show", "disallowed_packages"]).stdout

    assert {package["name"]: package["version"] for package in packages} == {
        "libmamba": "2.9.0",
        "libmambapy": "2.9.0",
    }
    assert "libmamba ==2.10.0" in config
    assert "libmambapy ==2.10.0" in config
