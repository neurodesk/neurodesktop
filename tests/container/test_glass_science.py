"""Installed Apptainer isolation with Glass's image-owned scientific options."""

import os
from pathlib import Path
import re
import select
import shlex
import shutil
import signal
import subprocess

import pytest


pytestmark = pytest.mark.skipif(
    os.getuid() == 0, reason="Scientific invocations run as the notebook user",
)


def _sandbox(root, name):
    sandbox = root / name
    for directory in ("dev", "proc", "sys", "tmp", "var/tmp", "home", "etc", "opt/state"):
        (sandbox / directory).mkdir(parents=True, exist_ok=True)
    (sandbox / "etc/passwd").write_text(
        f"scientist:x:{os.getuid()}:{os.getgid()}::/home/scientist:/bin/sh\n"
    )
    (sandbox / "etc/group").write_text(f"scientist:x:{os.getgid()}:\n")
    shell = Path("/bin/sh").resolve()
    dependencies = subprocess.check_output(["ldd", str(shell)], text=True)
    for source in {shell, *(Path(p) for p in re.findall(r"(/[^\s()]+)", dependencies))}:
        destination = sandbox / str(source).lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o755)
    (sandbox / "bin").mkdir(exist_ok=True)
    shutil.copyfile(shell, sandbox / "bin/sh")
    (sandbox / "bin/sh").chmod(0o755)
    return sandbox


def _check_isolation(root, environment):
    assert os.getuid() != 0, "Run the scientific probe as the notebook user"
    home = root / "home"
    home.mkdir()
    runtime = root / "runtime"
    runtime.mkdir()
    cpuinfo = runtime / "neurodesktop/cpuinfo"
    cpuinfo.parent.mkdir()
    cpuinfo.write_text("cpu MHz : 2245.778\n")
    temporary = root / "apptainer-tmp"
    temporary.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("APPTAINER", "SINGULARITY"))}
    env.update(HOME=str(home), XDG_RUNTIME_DIR=str(runtime), NEURODESKTOP_RUNTIME="glass")
    result = subprocess.run(
        ["bash", "-c", 'source "$1" >/dev/null; env -0', "glass-options", str(environment)],
        env=env, capture_output=True, check=True, timeout=30,
    )
    env = dict(entry.split("=", 1) for entry in result.stdout.decode().split("\0") if entry)
    options = shlex.split(env["neurodesk_singularity_opts"])
    assert options == ["--writable-tmpfs", "--bind", f"{cpuinfo}:/proc/cpuinfo:ro"]
    assert env["APPTAINERENV_MCR_CACHE_ROOT"] == str(home / ".cache/neurodesktop-mcr")
    env.update(APPTAINER_CACHEDIR=str(root / "cache"), APPTAINER_TMPDIR=str(temporary),
               APPTAINER_BINDPATH="", APPTAINER_BIND="", APPTAINER_NV="0")
    images = [_sandbox(root, name) for name in ("A", "B")]

    def command(image, script):
        return ["apptainer", "exec", "--userns", "--containall", *options,
                str(image), "/bin/sh", "-ec", script]

    processes = []
    try:
        for image in images:
            script = ('read -r cpu < /proc/cpuinfo; test "$cpu" = "cpu MHz : 2245.778"; '
                      'if (printf corrupt > /proc/cpuinfo) 2>/dev/null; then exit 1; fi; '
                      'test ! -e /opt/state/value; '
                      f'printf {image.name} > /opt/state/value; '
                      'printf "ready\\n"; read -r release; '
                      'read -r value < /opt/state/value || test -n "$value"; '
                      f'test "$value" = {image.name}; printf "%s\\n" "$value"')
            process = subprocess.Popen(command(image, script), env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, start_new_session=True)
            processes.append(process)
            assert select.select([process.stdout], [], [], 30)[0], "Apptainer never became ready"
            line = process.stdout.readline()
            if line != "ready\n":
                _, error = process.communicate(timeout=10)
                pytest.fail(f"Apptainer did not start: {line} {error}")
        for process, image in zip(processes, images, strict=True):
            output, error = process.communicate("release\n", timeout=30)
            assert process.returncode == 0, error
            assert output == image.name + "\n"
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=10)
    for image in images:
        result = subprocess.run(command(image, "test ! -e /opt/state/value; printf clean"),
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "clean"
        assert not (image / "opt/state/value").exists()
    assert cpuinfo.read_text() == "cpu MHz : 2245.778\n"
    assert not list(temporary.iterdir())
    assert not (runtime / "neurodesktop/apptainer_overlay").exists()


def test_glass_scientific_invocations_are_isolated_and_cleaned(tmp_path):
    if any(os.environ.get(name) for name in (
        "APPTAINER_CONTAINER", "SINGULARITY_CONTAINER", "APPTAINER_NAME", "SINGULARITY_NAME",
    )) or Path("/.apptainer.d").exists() or Path("/.singularity.d").exists():
        pytest.skip("Glass user namespaces require the regular or native profile, not nested HPC")
    _check_isolation(tmp_path, Path("/opt/neurodesktop/environment_variables.sh"))
