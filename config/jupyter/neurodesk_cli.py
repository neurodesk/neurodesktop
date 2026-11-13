#!/usr/bin/python3 -I
"""The `neurodesk` command: one entry point for the Neurodesktop helpers.

Each helper stays an independent executable under its original name, so its
privilege model, interpreter and existing callers (kernelspecs, the menu's
update.sh, agent guidance) are unchanged. This dispatcher only maps
`neurodesk <command>` onto that executable and adds three read-only commands
of its own: `check`, `test` and `info`.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

BIN_DIR = Path("/usr/local/bin")
TESTS_DIR = Path("/opt/tests")
TEST_PYTHON = Path("/opt/conda/bin/python")
CVMFS_REPOSITORY = Path("/cvmfs/neurodesk.ardc.edu.au")
DOCS_URL = "https://neurodesk.org/getting-started/neurodesktop/neurodesk-cli/"
GROUPS = ("Analysis", "Environment", "Agents")


@dataclass(frozen=True)
class Command:
    words: tuple[str, ...]
    group: str
    summary: str
    # Executable under BIN_DIR that implements the command; None for a
    # command the dispatcher runs itself.
    executable: str | None = None

    @property
    def name(self) -> str:
        return " ".join(self.words)


COMMANDS = (
    Command(("cite",), "Analysis", "Write CITATIONS.md for the Neurodesk tools you loaded", "neurodesk-cite"),
    Command(("kernel",), "Analysis", "Use a tool container's Python as a notebook kernel", "neurodesk-kernel"),
    Command(("uv",), "Analysis", "Create per-project uv notebook environments", "neurodesk-uv"),
    Command(("jobs",), "Analysis", "Run analyses on remote SSH/Slurm sites (experimental)", "neurodesk-jobs"),
    Command(("update",), "Environment", "Update the Neurodesk application menu", "neurodesk-update"),
    Command(("check",), "Environment", "Check CVMFS, GPU, Slurm and T3 remote access"),
    Command(("test",), "Environment", "Run the installed image test suite"),
    Command(("info",), "Environment", "Show image version, resources, GPU and CVMFS"),
    Command(("t3",), "Environment", "Set up T3 Code remote access through Tailscale", "t3_neurodesk_setup"),
    Command(("agent", "preflight"), "Agents", "Show modules and Slurm limits before agent analysis", "neurodesk-agent-preflight"),
    Command(("astra",), "Agents", "Publish ASTRA outputs and check their provenance", "neurodesk-astra-provenance"),
    Command(("lightcone", "materialize"), "Agents", "Materialize a Lightcone output outside sbatch", "neurodesk-lc-materialize"),
)


def usage() -> str:
    width = max(len(command.name) for command in COMMANDS) + 2
    lines = [
        "usage: neurodesk <command> [<args>]",
        "",
        "Neurodesk helper commands for Neurodesktop.",
    ]
    for group in GROUPS:
        lines += ["", f"{group}:"]
        lines += [f"  {command.name:<{width}} {command.summary}"
                  for command in COMMANDS if command.group == group]
    lines += [
        "",
        "Run 'neurodesk <command> --help' for the options of a command.",
        "Older names such as neurodesk-cite and neurodesk-kernel keep working.",
        f"Documentation: {DOCS_URL}",
    ]
    return "\n".join(lines) + "\n"


def find(words: Sequence[str]) -> tuple[Command | None, list[Command]]:
    """Return the command named by the leading words and the partial matches."""
    for command in COMMANDS:
        if tuple(words[:len(command.words)]) == command.words:
            return command, []
    partial = [command for command in COMMANDS if words and command.words[0] == words[0]]
    return None, partial


def unknown(name: str) -> int:
    names = sorted({command.words[0] for command in COMMANDS})
    message = f"neurodesk: unknown command '{name}'."
    close = difflib.get_close_matches(name, names, n=1)
    if close:
        message += f" Did you mean '{close[0]}'?"
    print(f"{message}\nRun 'neurodesk --help' to list commands.", file=sys.stderr)
    return 2


# --- check -----------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    status: str  # PASS, FAIL or SKIP
    detail: str


def run(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        return None
    except OSError as error:
        return subprocess.CompletedProcess(list(argv), 127, "", str(error))


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "no output")


# A hung FUSE mount blocks the caller forever, so every CVMFS read happens in
# a child process that a timeout can abandon.
CVMFS_PROBE = """
import os, sys
root = sys.argv[1]
print(len(os.listdir(os.path.join(root, "neurodesk-modules"))))
try:
    print(os.getxattr(root, "user.host").decode().strip())
except OSError:
    print("")
"""


def probe_cvmfs(timeout: float = 60) -> tuple[int, str] | str:
    """Return (module categories, active server) or a failure description."""
    result = run([sys.executable, "-I", "-c", CVMFS_PROBE, str(CVMFS_REPOSITORY)], timeout)
    if result is None:
        return f"no response from {CVMFS_REPOSITORY} within {timeout:.0f} s"
    if result.returncode != 0:
        return f"{CVMFS_REPOSITORY}/neurodesk-modules is not readable"
    count, _, host = result.stdout.partition("\n")
    return int(count), host.strip()


def check_cvmfs() -> Result:
    if os.environ.get("CVMFS_DISABLE", "").lower() in {"true", "1"}:
        return Result("SKIP", "CVMFS is disabled (CVMFS_DISABLE=true)")
    probed = probe_cvmfs()
    if isinstance(probed, str):
        return Result("FAIL", probed)
    count, host = probed
    if count == 0:
        return Result("FAIL", f"{CVMFS_REPOSITORY}/neurodesk-modules is empty")
    server = f" from {host}" if host else ""
    return Result("PASS", f"{count} module categories{server}")


def check_gpu() -> Result:
    gpu_check = BIN_DIR / "neurodesktop-gpu-check"
    if os.environ.get("DISPLAY") and gpu_check.exists():
        result = run([str(gpu_check)], 120)
        if result is None:
            return Result("FAIL", "the desktop rendering probe timed out")
        if result.returncode == 0:
            return Result("PASS", "NVIDIA OpenGL rendering works in this desktop")
        return Result("FAIL", first_line(result.stderr or result.stdout))
    if shutil.which("nvidia-smi") is None:
        return Result("SKIP", "no NVIDIA driver visible in this container")
    result = run(["nvidia-smi", "-L"], 30)
    if result is None or result.returncode != 0:
        return Result("FAIL", "nvidia-smi could not list devices")
    devices = [line for line in result.stdout.splitlines() if line.startswith("GPU ")]
    return Result("PASS", f"{len(devices)} NVIDIA device(s); run from a desktop "
                          "terminal to also check rendering")


def check_slurm() -> Result:
    if shutil.which("sinfo") is None:
        return Result("SKIP", "Slurm is not installed")
    result = run(["sinfo", "--noheader", "--format=%P %t"], 15)
    if result is None:
        return Result("FAIL", "sinfo timed out; the controller may still be starting")
    if result.returncode != 0:
        return Result("FAIL", first_line(result.stderr))
    partitions = [line.split()[0] for line in result.stdout.splitlines() if line.split()]
    return Result("PASS", f"partitions: {', '.join(dict.fromkeys(partitions)) or 'none'}")


def check_t3() -> Result:
    setup = BIN_DIR / "t3_neurodesk_setup"
    socket = Path(f"/tmp/tailscale-{os.geteuid()}/tailscaled.sock")
    if not setup.exists():
        return Result("SKIP", "T3 Code is not installed")
    if not socket.exists():
        return Result("SKIP", "remote access is not set up; run 'neurodesk t3' to set it up")
    result = run([str(setup), "--check"], 60)
    if result is None:
        return Result("FAIL", "the T3 check timed out")
    if result.returncode != 0:
        return Result("FAIL", first_line(result.stderr).removeprefix("Setup stopped: "))
    return Result("PASS", "T3 Code and Tailscale are configured")


CHECKS: dict[str, Callable[[], Result]] = {
    "cvmfs": check_cvmfs,
    "gpu": check_gpu,
    "slurm": check_slurm,
    "t3": check_t3,
}

CHECK_HELP = f"""usage: neurodesk check [{' | '.join(CHECKS)}] ...

Run read-only health checks; with no names, run all of them.
Each check prints PASS, FAIL or SKIP. The exit status is 1 when a check fails.

  cvmfs  the Neurodesk CVMFS repository is mounted and lists modules
  gpu    an NVIDIA GPU is visible; in a desktop terminal, it renders OpenGL
  slurm  the Slurm controller answers and lists partitions
  t3     T3 Code remote access through Tailscale is configured
"""


def command_check(args: Sequence[str]) -> int:
    if any(arg in {"-h", "--help"} for arg in args):
        print(CHECK_HELP, end="")
        return 0
    unknown_names = [name for name in args if name not in CHECKS]
    if unknown_names:
        print(f"neurodesk check: unknown check '{unknown_names[0]}'. "
              f"Choose from: {', '.join(CHECKS)}.", file=sys.stderr)
        return 2
    names = list(dict.fromkeys(args)) or list(CHECKS)
    failed = False
    for name in names:
        result = CHECKS[name]()
        failed |= result.status == "FAIL"
        print(f"[{result.status}] {name:<6} {result.detail}", flush=True)
    return 1 if failed else 0


# --- test ------------------------------------------------------------------

TEST_HELP = f"""usage: neurodesk test [PYTEST_ARGS ...]

Run the image's container test suite in {TESTS_DIR} with pytest.
With no arguments, run every test. The full suite takes a while and some
tests need network access, CVMFS or a running desktop. Pass pytest options to
narrow it, for example:

  neurodesk test -k cvmfs
  neurodesk test -x -q
"""


def command_test(args: Sequence[str], execv: Callable[[str, list[str]], object]) -> int:
    if any(arg in {"-h", "--help"} for arg in args):
        print(TEST_HELP, end="")
        return 0
    if not (TESTS_DIR / "pytest.ini").is_file():
        print(f"neurodesk test: no installed test suite at {TESTS_DIR}.", file=sys.stderr)
        return 1
    # The suite is root-owned, so pytest's cache cannot be written there.
    execv(str(TEST_PYTHON), [str(TEST_PYTHON), "-m", "pytest", "-p", "no:cacheprovider",
                             "--rootdir", str(TESTS_DIR), str(TESTS_DIR), *args])
    return 0


# --- info ------------------------------------------------------------------

def read_text(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def memory_limit() -> str:
    limit = read_text("/sys/fs/cgroup/memory.max")
    total_kib = next((int(line.split()[1]) for line in read_text("/proc/meminfo").splitlines()
                      if line.startswith("MemTotal:")), 0)
    limit_bytes = int(limit) if limit.isdigit() else total_kib * 1024
    if total_kib:
        limit_bytes = min(limit_bytes, total_kib * 1024)
    return f"{limit_bytes / 2**30:.1f} GiB" if limit_bytes else "unknown"


def cpu_limit() -> str:
    available = len(os.sched_getaffinity(0))
    quota, _, period = read_text("/sys/fs/cgroup/cpu.max").partition(" ")
    if quota.isdigit() and period.strip().isdigit() and int(period):
        return f"{min(available, int(quota) / int(period)):g}"
    return str(available)


def command_info(args: Sequence[str]) -> int:
    if args:
        print("usage: neurodesk info\n\nShow the image version, resources, GPU and CVMFS state.",
              file=sys.stdout if args[0] in {"-h", "--help"} else sys.stderr)
        return 0 if args[0] in {"-h", "--help"} else 2
    rows = [
        ("Neurodesktop", os.environ.get("NEURODESKTOP_VERSION", "unknown")),
        ("User", f"{os.environ.get('USER') or os.getuid()} (home {Path.home()})"),
        ("CPUs", cpu_limit()),
        ("Memory", memory_limit()),
    ]
    storage = Path("/neurodesktop-storage")
    if storage.is_dir():
        usage_ = shutil.disk_usage(storage)
        rows.append(("Storage", f"{storage}: {usage_.free / 2**30:.1f} GiB free "
                                f"of {usage_.total / 2**30:.1f} GiB"))
    gpu = run(["nvidia-smi", "-L"], 30) if shutil.which("nvidia-smi") else None
    devices = [line for line in gpu.stdout.splitlines() if line.startswith("GPU ")] if gpu else []
    rows.append(("GPU", "; ".join(devices) or "none visible"))
    if os.environ.get("CVMFS_DISABLE", "").lower() in {"true", "1"}:
        rows.append(("CVMFS", "disabled"))
    else:
        probed = probe_cvmfs(timeout=20)
        rows.append(("CVMFS", probed if isinstance(probed, str) else
                     f"mounted, server {probed[1] or 'unknown'}"))
    width = max(len(label) for label, _ in rows) + 1
    for label, value in rows:
        print(f"{label + ':':<{width}} {value}")
    return 0


# --- dispatch ----------------------------------------------------------------

def main(argv: Sequence[str] | None = None,
         execv: Callable[[str, list[str]], object] = os.execv) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(usage(), end="")
        return 0
    if args[0] == "--version":
        print(f"neurodesk {os.environ.get('NEURODESKTOP_VERSION', 'unknown')}")
        return 0
    if args[0] == "help":
        if len(args) == 1:
            print(usage(), end="")
            return 0
        args = [*args[1:], "--help"]

    command, partial = find(args)
    if command is None:
        if partial:
            names = ", ".join(f"'neurodesk {match.name}'" for match in partial)
            print(f"neurodesk {args[0]}: choose a command: {names}.", file=sys.stderr)
            return 2
        return unknown(args[0])
    rest = args[len(command.words):]
    if command.name == "check":
        return command_check(rest)
    if command.name == "test":
        return command_test(rest, execv)
    if command.name == "info":
        return command_info(rest)
    assert command.executable is not None
    target = BIN_DIR / command.executable
    if not os.access(target, os.X_OK):
        print(f"neurodesk {command.name}: {target} is not installed in this image.", file=sys.stderr)
        return 1
    execv(str(target), [str(target), *rest])
    return 0


if __name__ == "__main__":
    sys.exit(main())
