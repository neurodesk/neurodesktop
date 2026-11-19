"""Fail closed when the reviewed Slurm source or runtime no longer matches."""

import hashlib
import os
import subprocess

import pytest

from testlib import repo_path


def test_build_refuses_unreviewed_source_before_configuring(tmp_path):
    source = tmp_path / "source"
    (source / "src/common").mkdir(parents=True)
    env_source = source / "src/common/env.c"
    env_source.write_text("changed upstream source\n")
    configure = source / "configure"
    configure.write_text("#!/bin/sh\ntouch configured\n")
    configure.chmod(0o755)
    result = subprocess.run(
        ["bash", str(repo_path("config/slurm/build_env_compat.sh")), str(source), str(tmp_path / "out")],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "FAILED" in result.stdout
    assert not (source / "configured").exists()
    assert env_source.read_text() == "changed upstream source\n"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mode,enabled", [("host", "1"), ("local", "0")])
def test_nonlocal_setup_does_not_require_the_private_worker(mode, enabled):
    result = subprocess.run(
        ["bash", str(repo_path("config/slurm/setup_and_start_slurm.sh"))],
        env={**os.environ, "NEURODESKTOP_SLURM_MODE": mode, "NEURODESKTOP_SLURM_ENABLE": enabled},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "compatibility" not in result.stderr


def test_local_setup_refuses_changed_runtime_before_starting_services(tmp_path):
    script = repo_path("config/slurm/setup_and_start_slurm.sh").read_text()
    manifest = tmp_path / "compatibility.sha256"
    binary = tmp_path / "slurmd-package"
    binary.write_text("updated package\n")
    manifest.write_text(f"{'0' * 64}  {binary}\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("slurmd", "slurmctld", "munged"):
        stub = commands / name
        stub.write_text("#!/bin/sh\nexit 99\n")
        stub.chmod(0o755)
    script = script.replace('"${EUID}"', '"0"').replace(
        "/opt/neurodesktop/slurm-env/compatibility.sha256", str(manifest),
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}",
             "NEURODESKTOP_SLURM_MODE": "local", "NEURODESKTOP_SLURM_ENABLE": "1"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "Slurm packages changed" in result.stderr


PRIMARY_URL = "https://download.schedmd.com/slurm/slurm-25.11.2.tar.bz2"
FALLBACK_URL = (
    "https://github.com/SchedMD/slurm/releases/download/slurm-25-11-2-1/slurm-25.11.2.tar.bz2"
)
ARCHIVE = b"reviewed slurm release archive\n"
ARCHIVE_SHA256 = hashlib.sha256(ARCHIVE).hexdigest()


def fetch_source(tmp_path, served, version="25.11.2", sha256=ARCHIVE_SHA256):
    """Run the fetch script with a curl stand-in serving ``served`` by URL."""
    commands = tmp_path / "bin"
    commands.mkdir()
    content = tmp_path / "content"
    content.mkdir()
    for index, (url, body) in enumerate(served.items()):
        (content / f"{index}.body").write_bytes(body)
        (content / f"{index}.url").write_text(url)
    requests = tmp_path / "requests.log"
    curl = commands / "curl"
    curl.write_text(f"""#!/usr/bin/env bash
out=""; url=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o) out=$2; shift 2 ;;
        https://*) url=$1; shift ;;
        *) shift ;;
    esac
done
echo "$url" >> "{requests}"
for marker in "{content}"/*.url; do
    [[ -e "$marker" ]] || continue
    if [[ "$(cat "$marker")" == "$url" ]]; then
        cp "${{marker%.url}}.body" "$out"
        exit 0
    fi
done
echo "curl: (7) Failed to connect" >&2
exit 7
""")
    curl.chmod(0o755)
    output = tmp_path / "slurm.tar.bz2"
    result = subprocess.run(
        ["bash", str(repo_path("config/slurm/fetch_slurm_source.sh")), version, sha256, str(output)],
        env={**os.environ, "PATH": f"{commands}:{os.environ['PATH']}"},
        capture_output=True, text=True, timeout=10,
    )
    contacted = requests.read_text().split() if requests.exists() else []
    return result, output, contacted


def test_fetch_uses_schedmd_download_server_first(tmp_path):
    result, output, contacted = fetch_source(
        tmp_path, {PRIMARY_URL: ARCHIVE, FALLBACK_URL: ARCHIVE},
    )
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == ARCHIVE
    assert contacted == [PRIMARY_URL]


def test_fetch_falls_back_to_schedmd_github_release_when_server_unreachable(tmp_path):
    result, output, contacted = fetch_source(tmp_path, {FALLBACK_URL: ARCHIVE})
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == ARCHIVE
    assert contacted == [PRIMARY_URL, FALLBACK_URL]
    assert f"unavailable from {PRIMARY_URL}" in result.stderr


def test_fetch_refuses_fallback_archive_that_does_not_match_pin(tmp_path):
    result, output, contacted = fetch_source(tmp_path, {FALLBACK_URL: b"altered archive\n"})
    assert result.returncode != 0
    assert contacted == [PRIMARY_URL, FALLBACK_URL]
    assert "does not match the pinned SHA-256" in result.stderr
    assert not output.exists()
    assert not (tmp_path / "slurm.tar.bz2.partial").exists()


def test_fetch_reports_mismatched_primary_archive_instead_of_masking_it(tmp_path):
    result, output, contacted = fetch_source(
        tmp_path, {PRIMARY_URL: b"altered archive\n", FALLBACK_URL: ARCHIVE},
    )
    assert result.returncode != 0
    assert contacted == [PRIMARY_URL]
    assert not output.exists()


def test_fetch_fails_when_every_source_is_unavailable(tmp_path):
    result, output, contacted = fetch_source(tmp_path, {})
    assert result.returncode != 0
    assert contacted == [PRIMARY_URL, FALLBACK_URL]
    assert "unavailable from every source" in result.stderr
    assert not output.exists()
    assert not (tmp_path / "slurm.tar.bz2.partial").exists()


@pytest.mark.parametrize("version,sha256", [
    ("25.11.2-1", ARCHIVE_SHA256),
    ("25.11", ARCHIVE_SHA256),
    ("25.11.2", "0" * 63),
    ("25.11.2", ARCHIVE_SHA256.upper()),
])
def test_fetch_rejects_malformed_pins_before_downloading(tmp_path, version, sha256):
    result, output, contacted = fetch_source(
        tmp_path, {PRIMARY_URL: ARCHIVE}, version=version, sha256=sha256,
    )
    assert result.returncode == 2
    assert contacted == []
    assert not output.exists()


def test_private_worker_build_and_install_are_packaged():
    dockerfile = repo_path("Dockerfile").read_text()
    assert (
        'bash /tmp/slurm-env/fetch_slurm_source.sh "${SLURM_ENV_VERSION}" "${SLURM_ENV_SHA256}" '
        "/tmp/slurm.tar.bz2"
    ) in dockerfile
    assert "config/slurm/fetch_slurm_source.sh" in dockerfile
    assert "bash /tmp/slurm-env/build_env_compat.sh /tmp/slurm-source /opt/slurm-env" in dockerfile
    assert "FROM builder AS slurm-env-builder\nARG SLURM_ENV_VERSION=" in dockerfile
    assert "COPY --from=slurm-env-builder /opt/slurm-env/ /opt/neurodesktop/slurm-env/" in dockerfile
    assert "from=slurm-env-builder,source=/usr/bin/patchelf,target=/tmp/patchelf,ro" in dockerfile
    assert "bash /tmp/slurm/install_env_compat.sh /tmp/patchelf" in dockerfile
    assert repo_path("config/slurm/user-env-25.11.2.patch").is_file()
