"""Compile neurodesktop-shm-private and run it against real SysV segments."""

import ctypes
import subprocess
import sys

import pytest

from testlib import repo_path

# A child that creates a world-accessible segment the way Xvfb -shmem does,
# attaches it, reports its id, and holds it until stdin closes.
OWNER = r"""
import ctypes, sys
libc = ctypes.CDLL(None, use_errno=True)
libc.shmat.restype = ctypes.c_void_p
shmid = libc.shmget(0, 4096, 0o1000 | 0o777)
assert shmid >= 0, ctypes.get_errno()
assert libc.shmat(shmid, None, 0) != ctypes.c_void_p(-1).value
print(shmid, flush=True)
sys.stdin.read()
"""


@pytest.fixture(scope="module")
def helper(tmp_path_factory):
    binary = tmp_path_factory.mktemp("shm") / "neurodesktop-shm-private"
    subprocess.run(
        [
            "gcc",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-o",
            str(binary),
            str(repo_path("config/voxl/neurodesktop-shm-private.c")),
        ],
        check=True,
    )
    return binary


@pytest.fixture
def owner():
    proc = subprocess.Popen(
        [sys.executable, "-c", OWNER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    shmid = int(proc.stdout.readline())
    yield proc, shmid
    proc.stdin.close()
    proc.wait(timeout=10)
    ctypes.CDLL(None).shmctl(shmid, 0, None)  # IPC_RMID


def _segment(shmid):
    with open("/proc/sysvipc/shm", encoding="ascii") as table:
        for line in table.readlines()[1:]:
            fields = line.split()
            if int(fields[1]) == shmid:
                return {"mode": int(fields[2], 8) & 0o777, "nattch": int(fields[6])}
    raise AssertionError(f"segment {shmid} not found")


def test_restricts_the_framebuffer_to_its_owner(helper, owner):
    proc, shmid = owner
    assert _segment(shmid)["mode"] == 0o777

    result = subprocess.run([str(helper), str(proc.pid)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert _segment(shmid)["mode"] == 0o600


def test_refuses_a_framebuffer_another_process_attached(helper, owner):
    proc, shmid = owner
    libc = ctypes.CDLL(None, use_errno=True)
    libc.shmat.restype = ctypes.c_void_p
    address = libc.shmat(shmid, None, 0o10000)  # SHM_RDONLY
    assert address != ctypes.c_void_p(-1).value
    try:
        result = subprocess.run([str(helper), str(proc.pid)], capture_output=True, text=True)
    finally:
        libc.shmdt(ctypes.c_void_p(address))

    assert result.returncode == 2
    assert "2 attachments" in result.stderr


def test_reports_a_process_without_segments(helper):
    result = subprocess.run([str(helper), "1"], capture_output=True, text=True)
    assert result.returncode == 1
    assert "no shared-memory segment" in result.stderr
