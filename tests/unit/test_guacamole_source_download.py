"""Execute the source download and extraction from the Guacamole build layer."""

import io
import os
import re
import subprocess
import tarfile

import pytest

from testlib import repo_path


@pytest.mark.parametrize(
    ("available", "hosts", "succeeds"),
    [
        ("downloads.apache.org", ["downloads.apache.org"], True),
        ("archive.apache.org", ["downloads.apache.org", "archive.apache.org"], True),
        ("none", ["downloads.apache.org", "archive.apache.org"], False),
    ],
)
def test_source_download_uses_current_release_then_archive(tmp_path, available, hosts, succeeds):
    dockerfile = repo_path("Dockerfile").read_text()
    layers = re.findall(r"^[A-Z]+ .*?(?=^[A-Z]+ |\Z)", dockerfile, re.MULTILINE | re.DOTALL)
    layer, = [item for item in layers if "source=config/guacamole/" in item]
    command = layer.split("\n", 1)[1].split("&& rm", 1)[0].replace("\\\n", "")
    command = command.replace("/tmp", '"$DOWNLOAD_DIR"')
    archive = tmp_path / "fixture.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        entry = tarfile.TarInfo("downloaded")
        entry.size = len(b"release")
        bundle.addfile(entry, io.BytesIO(b"release"))

    driver = r'''
curl() {
    local host output
    while [ "$#" -gt 0 ]; do
        case "$1" in
            https://*) host="${1#https://}"; host="${host%%/*}" ;;
            -o) shift; output="$1" ;;
        esac
        shift
    done
    printf '%s\n' "$host" >> "$DOWNLOAD_DIR/calls"
    if [ "$host" = "$AVAILABLE" ]; then
        cp "$SOURCE_ARCHIVE" "$output"
    else
        printf 'incomplete transfer' > "$output"
        return 28
    fi
}
'''
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", driver + command],
        env={
            **os.environ,
            "GUACAMOLE_VERSION": "1.6.0",
            "DOWNLOAD_DIR": str(tmp_path),
            "SOURCE_ARCHIVE": str(archive),
            "AVAILABLE": available,
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert (tmp_path / "calls").read_text().splitlines() == hosts
    assert (result.returncode == 0) is succeeds, result.stderr
    extracted = tmp_path / "downloaded"
    assert extracted.exists() is succeeds
    if succeeds:
        assert extracted.read_text() == "release"
