"""Exercise the CI scanner installer with release-shaped download fixtures."""

import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile

import pytest
import yaml

from testlib import repo_path


@pytest.mark.parametrize("machine,asset_arch", [("x86_64", "64bit"), ("aarch64", "ARM64")])
@pytest.mark.parametrize("failure", ["", "checksum", "missing-checksum", "download"])
def test_install_verified_native_scanner(tmp_path, machine, asset_arch, failure):
    assets = tmp_path / "assets"
    assets.mkdir()
    archive = f"trivy_0.70.0_Linux-{asset_arch}.tar.gz"
    executable = b"#!/bin/sh\necho 'Version: 0.70.0'\n"
    with tarfile.open(assets / archive, "w:gz") as bundle:
        entry = tarfile.TarInfo("trivy")
        entry.mode = 0o755
        entry.size = len(executable)
        bundle.addfile(entry, io.BytesIO(executable))
    digest = hashlib.sha256((assets / archive).read_bytes()).hexdigest()
    if failure == "checksum":
        digest = "0" * 64
    checksums = f"{digest}  {archive}\n" if failure != "missing-checksum" else ""
    (assets / "trivy_0.70.0_checksums.txt").write_text(checksums)
    commands = tmp_path / "bin"
    commands.mkdir()
    (commands / "uname").write_text(f"#!/bin/sh\necho {machine}\n")
    (commands / "curl").write_text('''#!/bin/bash
set -eu
[[ " $* " == *" --retry-all-errors "* ]]
while [ "$#" -gt 0 ]; do
    case "$1" in
        https://github.com/aquasecurity/trivy/releases/download/v0.70.0/*) asset=${1##*/} ;;
        --output) shift; output=$1 ;;
    esac
    shift
done
[ "$FAILURE" != download ] || exit 6
cp "$ASSETS/$asset" "$output"
''')
    for command in commands.iterdir():
        command.chmod(0o755)
    path_file = tmp_path / "github-path"
    result = subprocess.run(
        ["bash", str(repo_path(".github/scripts/install_trivy.sh"))],
        capture_output=True, text=True, timeout=10,
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}",
             "RUNNER_TEMP": str(tmp_path), "GITHUB_PATH": str(path_file),
             "ASSETS": str(assets), "FAILURE": failure},
    )
    assert not list(tmp_path.glob("trivy-download.*"))
    if failure:
        assert result.returncode != 0
        assert not path_file.exists()
        assert not (tmp_path / "neurodesktop-trivy/trivy").exists()
    else:
        assert result.returncode == 0, result.stderr
        installed = Path(path_file.read_text().strip()) / "trivy"
        assert subprocess.check_output([str(installed), "--version"], text=True).strip() == "Version: 0.70.0"


def test_all_scan_actions_use_verified_installer():
    workflows = list(repo_path(".github/workflows").glob("*.yml"))
    scanners = 0
    for workflow in workflows:
        for job in yaml.safe_load(workflow.read_text()).get("jobs", {}).values():
            installed = False
            for step in job.get("steps", []):
                if step.get("run") == "bash .github/scripts/install_trivy.sh":
                    assert not step.get("if")
                    installed = True
                if step.get("uses", "").startswith("aquasecurity/trivy-action@"):
                    scanners += 1
                    assert installed, workflow.name
                    assert step["with"]["skip-setup-trivy"] is True
    assert scanners == 6
