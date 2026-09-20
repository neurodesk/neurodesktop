"""Exercise cleanup under the same errexit policy as GitHub Actions."""

import os
import subprocess

import pytest

from testlib import repo_path


@pytest.mark.parametrize('test_status', [0, 1, 23])
@pytest.mark.parametrize('cleanup_status', [0, 7])
@pytest.mark.parametrize('hpc', [False, True])
def test_cleanup_preserves_failure_and_removes_hpc_files(tmp_path, test_status, cleanup_status, hpc):
    """Exercise EXIT cleanup for successful tests, failed tests, and failed cleanup."""
    calls = tmp_path / 'calls'
    docker = tmp_path / 'docker'
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\nexit "$CLEANUP_STATUS"\n')
    docker.chmod(0o755)
    home = tmp_path / 'home'
    home.mkdir()
    passwd = tmp_path / 'passwd'
    passwd.touch()
    group = tmp_path / 'group'
    group.touch()
    env = {**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}',
           'CALLS': str(calls), 'CLEANUP_STATUS': str(cleanup_status),
           'TEST_STATUS': str(test_status), 'IMAGE_REF': 'candidate:run-1-1-amd64'}
    if hpc:
        env.update(HPC_HOME_DIR=str(home), HPC_PASSWD_FILE=str(passwd), HPC_GROUP_FILE=str(group))
    result = subprocess.run(
        ['bash', '-e', '-c', 'source "$1"; (exit "$TEST_STATUS")', 'test',
         str(repo_path('.github/scripts/image_test_cleanup.sh'))],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == (test_status or (1 if cleanup_status else 0))
    commands = calls.read_text().splitlines()
    assert commands[0] == 'rm -f neurodesktop-test'
    assert len(commands) == (2 if hpc else 1)
    if hpc:
        assert 'candidate:run-1-1-amd64' in commands[1]
        assert not home.exists()
        assert not passwd.exists()
        assert not group.exists()
