"""Regression coverage for concise Lmod availability listings."""

from testlib import repo_path


def test_image_hides_lmod_extensions_from_avail_by_default():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    assert "\nENV LMOD_AVAIL_EXTENSIONS=no\n" in dockerfile
