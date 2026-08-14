import os
import subprocess

from testlib import repo_path


def test_environment_variables_deduplicates_inherited_path(tmp_path):
    script = repo_path("config/jupyter/environment_variables.sh")
    home = tmp_path / "home"
    home.mkdir()
    inherited = "/opt/conda/bin:/usr/bin:/opt/conda/bin:/usr/local/sbin:/usr/bin"
    command = (
        f'PATH={inherited!r}; HOME={str(home)!r}; '
        f'source {str(script)!r} >/dev/null 2>&1; printf %s "$PATH"'
    )

    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "CVMFS_DISABLE": "true"},
    )

    entries = result.stdout.split(os.pathsep)
    assert entries == [
        "/usr/local/sbin",
        "/opt/conda/bin",
        "/usr/bin",
        str(home / ".local/bin"),
        "/opt/conda/condabin",
    ]
