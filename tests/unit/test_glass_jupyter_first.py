"""Jupyter and desktop share preparation, not a display dependency."""
import configparser

from testlib import load_source_module, repo_path


def test_preparation_runs_existing_hooks_without_launching_display(monkeypatch):
    runtime = load_source_module("glass_prepare", "/opt/neurodesktop/glass/runtime.py",
                                 "config/glass/runtime.py")
    env = {"HOME": "/home/jovyan", "NEURODESKTOP_RUNTIME": "glass"}
    monkeypatch.setattr(runtime, "environment", lambda: env)
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda args, **kw: calls.append((args, kw)))
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("preparation must not start Xorg")))
    runtime.prepare()
    assert len(calls) == 2
    assert calls[0][0] == ["/bin/bash", "/usr/local/bin/before-notebook.d/before_notebook.sh"]
    assert calls[1][0][-1] == "prepare"
    assert "--reuid=1000" in calls[1][0]
    assert all(kw["env"] == env and kw["check"] for _, kw in calls)


def test_systemd_dependencies_do_not_start_desktop_with_jupyter():
    def unit(name):
        parser = configparser.ConfigParser(strict=False)
        parser.read(repo_path(f"config/glass/{name}.service"))
        return parser
    jupyter = unit("neurodesktop-jupyter")
    desktop = unit("neurodesktop-glass")
    prepare = unit("neurodesktop-prepare")
    for service in (jupyter, desktop):
        assert service["Unit"]["Requires"].split() == ["neurodesktop-prepare.service"]
        assert "neurodesktop-glass.service" not in service["Unit"]["After"].split()
    assert prepare["Service"]["Type"] == "oneshot"
    assert prepare["Service"]["RemainAfterExit"] == "yes"
