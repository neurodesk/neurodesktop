import os
import subprocess

import pytest

from testlib import load_source_module


def git(directory, *arguments):
    return subprocess.run(
        ["git", "-C", str(directory), *arguments], check=True,
        text=True, capture_output=True,
    ).stdout.strip()


@pytest.fixture
def updater(tmp_path, monkeypatch):
    module = load_source_module(
        "neurodesk_update", "/usr/local/bin/neurodesk-update",
        "config/jupyter/neurodesk_update.py",
    )
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init")
    git(origin, "config", "user.email", "test@example.invalid")
    git(origin, "config", "user.name", "Test")
    (origin / ".gitignore").write_text("local/\nbuild-ran\n")
    (origin / "version").write_text("one")
    (origin / "build.sh").write_text(
        '#!/bin/bash\nset -eu\n'
        'printf "%s\\n" "$@" > build-ran\n'
        'test "$#" = 1 && test "$1" = --update\n'
        'git pull --ff-only\n'
        'mkdir -p local/bin\n'
        'printf "#!/bin/bash\\nsudo git pull\\n" > local/bin/update.sh\n'
        'cp version local/bin/unrelated.sh\n'
        'exit "${BUILD_STATUS:-0}"\n'
    )
    git(origin, "add", ".")
    git(origin, "commit", "-m", "Initial menu")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(origin), str(checkout))
    (checkout / "local/bin").mkdir(parents=True)
    monkeypatch.setattr(module, "CHECKOUT", checkout)
    monkeypatch.setattr(module.os, "geteuid", lambda: 1000)
    return module, origin, checkout


def advance(origin, version):
    (origin / "version").write_text(version)
    git(origin, "commit", "-am", version)
    return git(origin, "rev-parse", "HEAD")


def test_update_advances_git_and_restores_launcher_after_each_rebuild(updater):
    module, origin, checkout = updater
    module.install_update_launcher()
    launcher = checkout / "local/bin/update.sh"
    wrapper = launcher.read_text()
    assert "/usr/local/bin/neurodesk-update" in wrapper
    for version in ("two", "three"):
        revision = advance(origin, version)
        assert module.update_neurocommand() == 0
        assert git(checkout, "rev-parse", "HEAD") == revision
        assert (checkout / "build-ran").read_text() == "--update\n"
        assert (checkout / "local/bin/unrelated.sh").read_text() == version
        assert launcher.read_text() == wrapper
        assert os.access(launcher, os.X_OK)


def test_failed_rebuild_preserves_status_and_restores_launcher(updater, monkeypatch):
    module, _, checkout = updater
    module.install_update_launcher()
    launcher = checkout / "local/bin/update.sh"
    wrapper = launcher.read_text()
    monkeypatch.setenv("BUILD_STATUS", "23")
    assert module.update_neurocommand() == 23
    assert (checkout / "build-ran").exists()
    assert launcher.read_text() == wrapper
    assert (checkout / "local/bin/unrelated.sh").read_text() == "one"


def test_launcher_preserves_helper_failure_after_enter(updater, tmp_path):
    module, _, checkout = updater
    module.install_update_launcher()
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/bash\nexit 23\n")
    helper.chmod(0o755)
    launcher = tmp_path / "launcher.sh"
    launcher.write_text((checkout / "local/bin/update.sh").read_text().replace(
        "/usr/local/bin/neurodesk-update", str(helper),
    ))
    result = subprocess.run(["bash", str(launcher)], input="\n", text=True, capture_output=True)
    assert result.returncode == 23


def test_root_is_refused_before_checkout_code_runs(updater, monkeypatch):
    module, _, checkout = updater
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    with pytest.raises(PermissionError):
        module.update_neurocommand()
    assert not (checkout / "build-ran").exists()


def test_read_only_checkout_is_refused_before_build(updater):
    module, _, checkout = updater
    if os.getuid() == 0:
        pytest.skip("Permission denial requires an unprivileged test runner")
    checkout.chmod(0o555)
    try:
        with pytest.raises(PermissionError):
            module.update_neurocommand()
        assert not (checkout / "build-ran").exists()
    finally:
        checkout.chmod(0o755)
