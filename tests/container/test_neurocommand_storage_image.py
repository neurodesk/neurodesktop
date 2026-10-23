"""The image keeps bundled modules without nesting a shared-store symlink."""

from pathlib import Path


def test_neurocommand_container_store_has_no_nested_default_link():
    containers = Path("/neurocommand/local/containers")
    assert containers.is_dir() or containers.is_symlink()
    if not containers.is_symlink():
        nested = containers / "containers"
        assert not nested.is_symlink() or nested.readlink() != Path("/neurodesktop-storage/containers")
