"""Execute the inherited startup branches used by Glass and container sessions."""

import json
import os
import secrets
import shlex
import subprocess

import pytest

from testlib import repo_path


@pytest.mark.parametrize("runtime", [None, "container", "glass"])
def test_access_url_watcher_keeps_glass_token_out_of_inherited_logs(tmp_path, runtime):
    token = secrets.token_hex(24)
    (tmp_path / "jpserver-123.json").write_text(json.dumps({"port": 8888, "token": token}))
    commands = tmp_path / "bin"
    commands.mkdir()
    curl = commands / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    startup = repo_path("config/jupyter/before_notebook.sh").read_text()
    branch = startup.split("# Reprint the Jupyter access URL", 1)[1].split(
        "# Ensure the VNC password", 1
    )[0]
    branch = "# Reprint the Jupyter access URL" + branch
    watcher = tmp_path / "print_access_url.sh"
    watcher.write_text(repo_path("config/jupyter/print_access_url.sh").read_text())
    watcher.chmod(0o755)
    branch = branch.replace("/opt/neurodesktop/print_access_url.sh", str(watcher))
    env = {**os.environ, "PATH": f"{commands}:{os.environ['PATH']}",
           "HOME": str(tmp_path), "JUPYTER_RUNTIME_DIR": str(tmp_path),
           "NEURODESKTOP_PRINT_ACCESS_URL": "1", "NEURODESKTOP_ACCESS_URL_SETTLE": "0"}
    env.pop("NEURODESKTOP_RUNTIME", None)
    if runtime is not None:
        env["NEURODESKTOP_RUNTIME"] = runtime
    result = subprocess.run(["bash", "-c", branch + "\nwait"], env=env,
                            capture_output=True, text=True, timeout=10, check=True)
    logs = result.stdout + result.stderr
    if runtime == "glass":
        assert token not in logs
        assert "Neurodesktop is ready" not in logs
    else:
        assert f"http://localhost:8888/lab?token={token}" in logs


@pytest.mark.parametrize("runtime,hpc,macos,has_cpuinfo,expected", [
    ("glass", False, False, True, ["--writable-tmpfs"]),
    ("glass", False, False, False, ["--writable-tmpfs"]),
    ("container", False, False, False, []),
    ("container", False, True, False, ["--overlay", "/tmp/apptainer_overlay"]),
    ("container", True, False, False, ["--writable-tmpfs"]),
])
def test_scientific_options(tmp_path, runtime, hpc, macos, has_cpuinfo, expected):
    home = tmp_path / "home"
    home.mkdir()
    cache_marker = home / ".cache/neurodesktop-mcr/previous-session"
    cache_marker.parent.mkdir(parents=True)
    cache_marker.write_text("keep compiled MATLAB cache")
    state = tmp_path / "runtime" / "neurodesktop"
    state.mkdir(parents=True)
    cpuinfo = state / "cpuinfo"
    if has_cpuinfo:
        cpuinfo.write_text("cpu MHz : 2245.778\n")
    source = repo_path("config/jupyter/environment_variables.sh").read_text()
    branch = source.split("# This is needed to make containers writable", 1)[1].split(
        "# export neurodesk_singularity_opts", 1
    )[0]
    branch = "# This is needed to make containers writable" + branch
    branch = branch.replace('"${EUID}"', '"1000"')
    script = (f"is_apptainer_runtime() {{ {'true' if hpc else 'false'}; }}\n"
              f"is_macos_host_runtime() {{ {'true' if macos else 'false'}; }}\n" + branch +
              "\nprintf '%s\\n' \"$neurodesk_singularity_opts\" \"$MCR_CACHE_ROOT\" "
              "\"$APPTAINERENV_MCR_CACHE_ROOT\" \"$SINGULARITYENV_MCR_CACHE_ROOT\"")
    env = {"PATH": os.environ["PATH"], "HOME": str(home),
           "XDG_RUNTIME_DIR": str(state.parent), "NEURODESKTOP_RUNTIME": runtime}
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True,
                            text=True, timeout=10, check=True)
    options, cache, apptainer_cache, singularity_cache = result.stdout.splitlines()
    if has_cpuinfo:
        expected = [*expected, "--bind", f"{cpuinfo}:/proc/cpuinfo:ro"]
    assert shlex.split(options) == expected
    assert not (state / "apptainer_overlay").exists()
    assert cache_marker.read_text() == "keep compiled MATLAB cache"
    if runtime == "glass":
        assert cache == apptainer_cache == singularity_cache == str(home / ".cache/neurodesktop-mcr")
        assert (home / ".cache/neurodesktop-mcr").stat().st_mode & 0o777 == 0o700
