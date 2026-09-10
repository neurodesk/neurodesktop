"""Contracts for the test and worked-example assets copied into the image."""

from testlib import repo_path


DOCKERFILE = repo_path("Dockerfile").read_text(encoding="utf-8")


def test_image_assets_are_readable_independent_of_checkout_umask():
    copy_tests = DOCKERFILE.index("cp -a /tmp/tests/container /opt/tests")
    normalize = DOCKERFILE.index(
        "chmod -R a+rX /opt/neurodesktop/examples /opt/tests"
    )
    next_layer = DOCKERFILE.index(
        "source=config/jupyter/patch_jupyter_server_proxy.py", copy_tests
    )

    assert copy_tests < normalize < next_layer
