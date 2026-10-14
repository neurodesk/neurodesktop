import importlib.util
import subprocess
import sys

import pytest

from testlib import repo_path


@pytest.fixture
def driver():
    spec = importlib.util.spec_from_file_location(
        "native_desktop_driver", repo_path("tests/container/native_desktop_driver.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("chunks", [(b"12", b"3\n"), (b"123", b"\n")])
def test_display_announcement_waits_for_complete_line(tmp_path, monkeypatch, driver, chunks):
    popen = subprocess.Popen
    children = []

    def start_writer(args, **kwargs):
        writer = args[args.index("-displayfd") + 1]
        child = popen(
            [sys.executable, "-c", f"""
import os, time
for chunk in {chunks!r}:
    os.write({writer}, chunk)
    time.sleep(0.1)
"""], **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(driver.subprocess, "Popen", start_writer)
    try:
        with (tmp_path / "display.log").open("w") as log:
            server, display = driver.start_display(log)
            assert display == ":123"
            assert server.wait(timeout=5) == 0, (tmp_path / "display.log").read_text()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize("announcement", [b"123", b"123 junk\n", b"\xff\n", b"", b"1" * 100])
def test_display_rejects_incomplete_or_invalid_announcement(tmp_path, monkeypatch, driver, announcement):
    popen = subprocess.Popen
    children = []

    def start_writer(args, **kwargs):
        writer = args[args.index("-displayfd") + 1]
        child = popen(
            [sys.executable, "-c", f"import os; os.write({writer}, {announcement!r})"],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(driver.subprocess, "Popen", start_writer)
    try:
        with (tmp_path / "display.log").open("w") as log:
            with pytest.raises(AssertionError, match="did not announce"):
                driver.start_display(log)
        assert len(children) == 2
        assert all(child.poll() is not None for child in children)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


def test_display_deadline_covers_the_whole_announcement(tmp_path, monkeypatch, driver):
    import itertools
    from types import SimpleNamespace

    popen = subprocess.Popen
    children = []
    clock = itertools.count(step=11)
    monkeypatch.setattr(driver, "time", SimpleNamespace(monotonic=lambda: next(clock)))

    def start_writer(args, **kwargs):
        writer = args[args.index("-displayfd") + 1]
        child = popen(
            [sys.executable, "-c",
             f"import os, time; os.write({writer}, b'123'); time.sleep(60)"],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(driver.subprocess, "Popen", start_writer)
    try:
        with (tmp_path / "display.log").open("w") as log:
            with pytest.raises(AssertionError, match="did not announce"):
                driver.start_display(log)
        assert len(children) == 2
        assert all(child.returncode == -9 for child in children)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
