"""Tests for mount point accessibility and CRUD operations."""
import os
import shutil

import pytest

MOUNT_PATHS = ["/neurodesktop-storage", "/home/jovyan"]


@pytest.fixture(
    params=[p for p in MOUNT_PATHS if os.path.exists(p)], ids=lambda p: p.strip("/")
)
def mount_path(request):
    """Parametrize over available mount paths."""
    return request.param


class TestMountCRUD:
    """CRUD operations on mount points."""

    def test_mount_exists_and_writable(self, mount_path):
        """Mount point exists and is writable."""
        assert os.path.isdir(mount_path)
        assert os.access(mount_path, os.W_OK)

    def test_create_and_read_file(self, mount_path):
        """Create a file and read it back."""
        test_file = os.path.join(mount_path, "ci-mount-test.txt")
        try:
            with open(test_file, "w") as f:
                f.write("mount-test-ok")
            with open(test_file, "r") as f:
                assert f.read() == "mount-test-ok"
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)

    def test_modify_file(self, mount_path):
        """Create, append to, and verify file content."""
        test_file = os.path.join(mount_path, "ci-mount-modify.txt")
        try:
            with open(test_file, "w") as f:
                f.write("original\n")
            with open(test_file, "a") as f:
                f.write("appended\n")
            with open(test_file, "r") as f:
                content = f.read()
            assert "original" in content
            assert "appended" in content
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)

    def test_nested_directory(self, mount_path):
        """Create nested directory structure."""
        subdir = os.path.join(mount_path, "ci-subdir")
        nested_file = os.path.join(subdir, "nested.txt")
        try:
            os.makedirs(subdir, exist_ok=True)
            with open(nested_file, "w") as f:
                f.write("nested-ok")
            assert os.path.isfile(nested_file)
            with open(nested_file, "r") as f:
                assert f.read() == "nested-ok"
        finally:
            if os.path.exists(subdir):
                shutil.rmtree(subdir)

    def test_delete_file(self, mount_path):
        """Create and delete a file."""
        test_file = os.path.join(mount_path, "ci-mount-delete.txt")
        with open(test_file, "w") as f:
            f.write("to-delete")
        assert os.path.exists(test_file)
        os.remove(test_file)
        assert not os.path.exists(test_file)

    def test_delete_directory(self, mount_path):
        """Create and delete a directory tree."""
        subdir = os.path.join(mount_path, "ci-delete-dir")
        os.makedirs(os.path.join(subdir, "child"), exist_ok=True)
        with open(os.path.join(subdir, "child", "f.txt"), "w") as f:
            f.write("x")
        assert os.path.isdir(subdir)
        shutil.rmtree(subdir)
        assert not os.path.exists(subdir)
