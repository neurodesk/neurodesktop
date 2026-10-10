"""Exercise platform rejection and failed binary verification without downloads."""

import os
import subprocess
import sys

from testlib import repo_path


def run_installer(tmp_path, platform, destination):
    tools = tmp_path / "adapters"
    tools.mkdir()
    uname = tools / "uname"
    uname.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"print({platform!r} if sys.argv[1] == '-s' else 'x86_64')\n"
    )
    uname.chmod(0o755)
    curl = tools / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "output = sys.argv[sys.argv.index('--output') + 1]\n"
        "pathlib.Path(output).write_bytes(b'unverified download')\n"
    )
    curl.chmod(0o755)
    return subprocess.run(
        ["bash", str(repo_path("scripts/install_quality_tools.sh")), str(destination)],
        env=dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}", TMPDIR=str(tmp_path)),
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_unsupported_platform_does_not_create_destination(tmp_path):
    destination = tmp_path / "installed"
    result = run_installer(tmp_path, "Darwin", destination)
    assert result.returncode == 2
    assert "supports Linux only" in result.stderr
    assert not destination.exists()


def test_checksum_mismatch_preserves_previously_installed_tools(tmp_path):
    destination = tmp_path / "installed"
    destination.mkdir()
    original = {}
    for name in ("shellcheck", "actionlint", "shfmt"):
        original[name] = f"previous verified {name}".encode()
        (destination / name).write_bytes(original[name])
    result = run_installer(tmp_path, "Linux", destination)
    assert result.returncode != 0
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["adapters", "installed"]
