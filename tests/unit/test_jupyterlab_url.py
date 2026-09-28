"""The browser-test URL check accepts document routes within the same Lab app."""

import pytest

from testlib import assert_jupyterlab_url


@pytest.mark.parametrize("base", ["/", "/user/workspace-test/"])
@pytest.mark.parametrize("route", [
    "", "/", "?token=test", "/tree/a%20report.md", "/tree/report.html#heading",
    "/workspaces/reports/tree/a%20report.md",
])
def test_accepts_jupyterlab_routes(base, route):
    lab_url = f"http://127.0.0.1:44739{base}lab"
    assert_jupyterlab_url(lab_url + route, lab_url)


@pytest.mark.parametrize("base", ["/", "/user/workspace-test/"])
@pytest.mark.parametrize("destination", [
    "http://127.0.0.1:44739/home/jovyan/a%20report.md",
    "http://127.0.0.1:44739{base}files/a%20report.md",
    "http://127.0.0.1:44739{base}lab-other/tree/a%20report.md",
    "http://127.0.0.1:44739/user/another-user/lab/tree/a%20report.md",
    "http://127.0.0.1:44740{base}lab/tree/a%20report.md",
    "https://127.0.0.1:44739{base}lab/tree/a%20report.md",
    "http://example.org:44739{base}lab/tree/a%20report.md",
])
def test_rejects_navigation_outside_jupyterlab(base, destination):
    with pytest.raises(AssertionError):
        assert_jupyterlab_url(destination.format(base=base), f"http://127.0.0.1:44739{base}lab")


def test_rejects_leaving_user_prefix_for_root_lab():
    with pytest.raises(AssertionError):
        assert_jupyterlab_url(
            "http://127.0.0.1:44739/lab/tree/a%20report.md",
            "http://127.0.0.1:44739/user/workspace-test/lab",
        )
