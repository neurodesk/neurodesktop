import os
import subprocess

from testlib import REPO_ROOT, repo_path


SCRIPT = repo_path("scripts/analyze_image_size.sh")


def _write_executable(path, contents):
    path.write_text(contents)
    path.chmod(0o755)


def test_analyze_image_size_builds_the_checkout_from_any_working_directory(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "docker-calls.log"

    _write_executable(
        bin_dir / "docker",
        '#!/bin/sh\nprintf \'%s|%s\\n\' "$PWD" "$*" >> "$CALL_LOG"\n',
    )
    _write_executable(bin_dir / "dive", "#!/bin/sh\nexit 0\n")

    env = os.environ.copy()
    env["CALL_LOG"] = str(call_log)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        input="nn",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert call_log.read_text().splitlines() == [
        f"{tmp_path}|build {REPO_ROOT} -t neurodesktop:latest",
        f"{tmp_path}|images neurodesktop:latest",
    ]
