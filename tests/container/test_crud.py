"""The image promises writable home and storage directories in every profile."""
from pathlib import Path
import tempfile

import pytest


@pytest.mark.parametrize("mount_path", [Path("/neurodesktop-storage"), Path("/home/jovyan")], ids=["storage", "home"])
def test_mount_crud_lifecycle(mount_path):
    assert mount_path.is_dir(), f"Required directory missing: {mount_path}"
    with tempfile.TemporaryDirectory(prefix="neurodesktop-test-", dir=mount_path) as directory:
        nested = Path(directory) / "child"
        nested.mkdir()
        document = nested / "data.txt"
        document.write_text("original\n")
        assert document.read_text() == "original\n"
        with document.open("a") as stream:
            stream.write("appended\n")
        assert document.read_text() == "original\nappended\n"
        document.unlink()
        assert not document.exists()
        nested.rmdir()
        assert not nested.exists()
    assert not Path(directory).exists()
