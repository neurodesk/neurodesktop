"""Drive the `neurodesk` dispatcher against stand-in helper executables."""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from testlib import repo_path

CLI = repo_path("config/jupyter/neurodesk_cli.py")

# Runs the real dispatcher, including its os.execv, with BIN_DIR, TESTS_DIR and
# the CVMFS repository pointed at the test's own directories.
RUNNER = """
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("neurodesk_cli", sys.argv[1])
cli = sys.modules["neurodesk_cli"] = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
cli.BIN_DIR = Path(sys.argv[2])
cli.TESTS_DIR = Path(sys.argv[3])
cli.CVMFS_REPOSITORY = Path(sys.argv[4])
sys.exit(cli.main(sys.argv[5:]))
"""


def load_cli():
    spec = importlib.util.spec_from_file_location("neurodesk_cli", CLI)
    module = sys.modules["neurodesk_cli"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMANDS = load_cli().COMMANDS
EXTERNAL = [command for command in COMMANDS if command.executable]


class Neurodesk:
    def __init__(self, root: Path):
        self.bin = root / "bin"
        self.tests = root / "opt-tests"
        self.cvmfs = root / "cvmfs/neurodesk.ardc.edu.au"
        self.path = root / "path"
        for directory in (self.bin, self.tests, self.path):
            directory.mkdir(parents=True)
        # Only stand-ins are on PATH, so host GPUs or Slurm never leak in.
        self.env = {"PATH": str(self.path), "HOME": str(root),
                    "NEURODESKTOP_VERSION": "2026-11-13"}

    def executable(self, directory: Path, name: str, body: str) -> Path:
        path = directory / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
        return path

    def helper(self, name: str) -> Path:
        return self.executable(self.bin, name, f'printf "%s\\n" "{name}" "$@"')

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-c", RUNNER, str(CLI), str(self.bin), str(self.tests),
             str(self.cvmfs), *args],
            capture_output=True, text=True, timeout=60, env={**self.env, **env}, check=False)


@pytest.fixture
def neurodesk(tmp_path):
    return Neurodesk(tmp_path)


def test_help_lists_every_command_by_group(neurodesk):
    result = neurodesk.run("--help")
    assert result.returncode == 0
    for group in ("Analysis:", "Environment:", "Agents:"):
        assert f"\n{group}\n" in result.stdout
    for command in COMMANDS:
        assert re.search(rf"^  {re.escape(command.name)} +{re.escape(command.summary)}$",
                         result.stdout, re.MULTILINE), command.name
    assert "neurodesk-cite" in result.stdout
    assert neurodesk.run().stdout == result.stdout


@pytest.mark.parametrize("command", EXTERNAL, ids=lambda command: command.name)
def test_command_runs_its_helper_with_remaining_arguments(neurodesk, command):
    neurodesk.helper(command.executable)
    result = neurodesk.run(*command.words, "--flag", "two words")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [command.executable, "--flag", "two words"]


def test_help_subcommand_asks_the_helper_for_its_help(neurodesk):
    neurodesk.helper("neurodesk-cite")
    assert neurodesk.run("help", "cite").stdout.splitlines() == ["neurodesk-cite", "--help"]


def test_group_word_alone_names_its_commands(neurodesk):
    result = neurodesk.run("agent")
    assert result.returncode == 2
    assert "'neurodesk agent preflight'" in result.stderr


def test_unknown_command_suggests_the_closest_name(neurodesk):
    result = neurodesk.run("cit")
    assert result.returncode == 2
    assert "Did you mean 'cite'?" in result.stderr


def test_missing_helper_is_reported_without_a_traceback(neurodesk):
    result = neurodesk.run("kernel", "list")
    assert result.returncode == 1
    assert "is not installed in this image" in result.stderr
    assert "Traceback" not in result.stderr


def test_version_reports_the_image_version(neurodesk):
    assert neurodesk.run("--version").stdout == "neurodesk 2026-11-13\n"


def test_every_helper_is_installed_under_its_registered_name():
    dockerfile = repo_path("Dockerfile").read_text()
    for command in EXTERNAL:
        assert re.search(rf"/usr/local/bin/{re.escape(command.executable)}(?:\s|$)",
                         dockerfile), command.executable
    assert "/tmp/jupyter/neurodesk_cli.py /usr/local/bin/neurodesk " in dockerfile


def test_every_installed_neurodesk_helper_is_a_neurodesk_command():
    installed = set(re.findall(r"/usr/local/bin/(neurodesk-[\w-]+)", repo_path("Dockerfile").read_text()))
    assert installed <= {command.executable for command in EXTERNAL}


# --- test ----------------------------------------------------------------


def test_test_runs_the_full_installed_suite_with_extra_arguments(neurodesk, monkeypatch):
    cli = load_cli()
    (neurodesk.tests / "pytest.ini").write_text("[pytest]\n")
    monkeypatch.setattr(cli, "TESTS_DIR", neurodesk.tests)
    calls = []
    assert cli.main(["test", "-k", "cvmfs"], execv=lambda path, argv: calls.append(argv)) == 0
    [argv] = calls
    assert argv[1:4] == ["-m", "pytest", "-p"]
    assert argv[-3:] == [str(neurodesk.tests), "-k", "cvmfs"]


def test_test_without_installed_suite_fails(neurodesk):
    result = neurodesk.run("test")
    assert result.returncode == 1
    assert "no installed test suite" in result.stderr


# --- check ---------------------------------------------------------------


def test_cvmfs_check_passes_for_a_readable_repository(neurodesk):
    (neurodesk.cvmfs / "neurodesk-modules/functional").mkdir(parents=True)
    result = neurodesk.run("check", "cvmfs")
    assert result.returncode == 0, result.stdout
    assert result.stdout == "[PASS] cvmfs  1 module categories\n"


def test_cvmfs_check_fails_when_repository_is_missing(neurodesk):
    result = neurodesk.run("check", "cvmfs")
    assert result.returncode == 1
    assert result.stdout.startswith("[FAIL] cvmfs ")


def test_cvmfs_check_skips_when_disabled(neurodesk):
    result = neurodesk.run("check", "cvmfs", CVMFS_DISABLE="true")
    assert result.returncode == 0
    assert result.stdout.startswith("[SKIP] cvmfs ")


def test_slurm_check_reports_partitions(neurodesk):
    neurodesk.executable(neurodesk.path, "sinfo", 'printf "batch* idle\\nbatch* mix\\ngpu idle\\n"')
    result = neurodesk.run("check", "slurm")
    assert result.returncode == 0
    assert result.stdout == "[PASS] slurm  partitions: batch*, gpu\n"


def test_slurm_check_fails_with_the_controller_error(neurodesk):
    neurodesk.executable(neurodesk.path, "sinfo", 'echo "slurm_load_partitions: Unable to contact" >&2; exit 1')
    result = neurodesk.run("check", "slurm")
    assert result.returncode == 1
    assert result.stdout == "[FAIL] slurm  slurm_load_partitions: Unable to contact\n"


def test_all_checks_run_and_skips_do_not_fail(neurodesk):
    (neurodesk.cvmfs / "neurodesk-modules/functional").mkdir(parents=True)
    result = neurodesk.run("check", DISPLAY="")
    assert result.returncode == 0, result.stdout
    assert [line.split()[:2] for line in result.stdout.splitlines()] == [
        ["[PASS]", "cvmfs"], ["[SKIP]", "gpu"], ["[SKIP]", "slurm"], ["[SKIP]", "t3"]]


def test_unknown_check_is_rejected(neurodesk):
    result = neurodesk.run("check", "network")
    assert result.returncode == 2
    assert "unknown check 'network'" in result.stderr


# --- info ----------------------------------------------------------------


def test_info_reports_version_and_cvmfs_state(neurodesk):
    result = neurodesk.run("info", CVMFS_DISABLE="true")
    assert result.returncode == 0, result.stderr
    fields = dict(line.split(":", 1) for line in result.stdout.splitlines())
    assert fields["Neurodesktop"].strip() == "2026-11-13"
    assert fields["CVMFS"].strip() == "disabled"
    assert float(fields["CPUs"]) > 0
    assert set(fields) >= {"User", "Memory", "GPU"}


def test_installed_dispatcher_uses_the_isolated_system_python():
    assert CLI.read_text().startswith("#!/usr/bin/python3 -I\n")
    assert os.access(CLI, os.X_OK)


def test_gpu_check_skips_a_cpu_only_desktop(neurodesk):
    neurodesk.executable(neurodesk.bin, "neurodesktop-gpu-check", "echo 'no NVIDIA vendor' >&2; exit 1")
    result = neurodesk.run("check", "gpu", DISPLAY=":1")
    assert result.returncode == 0
    assert result.stdout.startswith("[SKIP] gpu ")


def test_gpu_check_runs_the_rendering_probe_when_a_gpu_is_visible(neurodesk):
    neurodesk.executable(neurodesk.path, "nvidia-smi", 'echo "GPU 0: Test GPU (UUID: GPU-1)"')
    neurodesk.executable(neurodesk.bin, "neurodesktop-gpu-check", "echo 'no NVIDIA vendor' >&2; exit 1")
    result = neurodesk.run("check", "gpu", DISPLAY=":1")
    assert result.returncode == 1
    assert result.stdout == "[FAIL] gpu    no NVIDIA vendor\n"

