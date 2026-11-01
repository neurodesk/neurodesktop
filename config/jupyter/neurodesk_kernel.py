#!/opt/conda/bin/python3
"""Register the Python inside a Neurodesk tool container as a Jupyter kernel.

Each container module puts a directory of command wrappers on PATH; every
wrapper runs its command through transparent-singularity's
``neurodesk_container exec``, which owns the bind paths, GPU, overlay and
display handling. A kernelspec that launches the module's ``python3`` wrapper
therefore runs ipykernel inside the container with the same runtime options as
any other Neurodesk command, and needs nothing installed outside it.

Specs record the absolute wrapper path rather than a module name: the wrapper
runs with ``--cleanenv``, so loading the module at launch would add nothing,
and a bare ``python3`` would silently fall back to the image's own Python
whenever the module was not loaded.

A wrapper belongs to the requested module when the container image it runs is
named after that module and its build date: ``fsl/6.0.7.16`` runs
``fsl_6.0.7.16_<YYYYMMDD>.simg``.
Searching the loaded PATH for such a wrapper never admits a dependency's
container or the image's own Python, and needs no parsing of modulefiles.
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

KERNEL_WRAPPER = "/opt/neurodesktop/kernel_wrapper.sh"
ENVIRONMENT_SCRIPT = "/opt/neurodesktop/environment_variables.sh"
MODULE_INIT = "/usr/share/module.sh"
PYTHON_COMMANDS = ("python3", "python")
# The first run of a CVMFS container fetches its files on demand.
PROBE_TIMEOUT_SECONDS = 600
MODULE_TIMEOUT_SECONDS = 300
SPEC_MARKER = "neurodesk_container"
KERNEL_PREFIX = "neurodesk-"

# Exit status 3 separates "Python runs but cannot import ipykernel" from a
# container or runtime failure, which never reaches this code.
NO_IPYKERNEL_STATUS = 3
PROBE_CODE = f"""
import sys
print("python %d.%d" % sys.version_info[:2], flush=True)
try:
    import ipykernel_launcher
except ImportError as error:
    print("no-ipykernel", error, flush=True)
    sys.exit({NO_IPYKERNEL_STATUS})
"""

RESOLVE_SCRIPT = r"""
source "$1" >/dev/null 2>&1
source "$2" >/dev/null 2>&1 || exit 3
module load "$3" >&2 || exit 4
tool=${3%%/*}
name=
IFS=: read -r -a loaded <<< "${LOADEDMODULES:-}"
for entry in "${loaded[@]}"; do [ "${entry%%/*}" = "$tool" ] && name=$entry; done
[ -n "$name" ] || exit 5
printf '%s\0%s' "$name" "$PATH"
"""
RESOLVE_ERRORS = {
    3: "Lmod is unavailable: could not source {init}.",
    4: "could not load module {name!r}. Run 'ml av' to list available modules.",
    5: "module {name!r} loaded but is not listed as loaded.",
}
# transparent-singularity wrappers run their command through the container
# runtime with the image path on the same line.
CONTAINER_EXEC = re.compile(
    r"^[^#\n]*\b(?:neurodesk_container|apptainer|singularity)\b[^\n]*\bexec\b[^\n]*?"
    r"([A-Za-z0-9._+-]+)\.(?:simg|sif)\b", re.MULTILINE)
WRAPPER_SIZE_LIMIT = 64 * 1024


class KernelError(Exception):
    """A user-facing failure, reported without a traceback."""


@dataclass(frozen=True)
class LoadedModule:
    name: str
    path: tuple[str, ...]

    @property
    def image_prefix(self) -> str:
        return self.name.replace("/", "_") + "_"

    def owns_image(self, image: str) -> bool:
        # Exact match up to the build date: a bare prefix would also accept
        # amico_2.1.0_arm64_<date>, the image of module amico_2.1.0/arm64.
        return re.fullmatch(re.escape(self.image_prefix) + r"\d{8}", image) is not None


@dataclass(frozen=True)
class ProbeFailure:
    wrapper: str
    reason: str
    missing_ipykernel: bool


@dataclass(frozen=True)
class ContainerPython:
    module: str
    wrapper: str
    version: str


def kernels_dir() -> Path:
    from jupyter_core.paths import jupyter_data_dir

    return Path(jupyter_data_dir()) / "kernels"


def kernel_name_for(module: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", module.lower()).strip("-.")
    return KERNEL_PREFIX + slug


def run_bounded(argv: list[str], timeout: int, stderr: int | None = subprocess.PIPE
                ) -> subprocess.CompletedProcess[str]:
    """Run *argv* in its own session and kill the whole session on timeout.

    Wrappers start Apptainer and the container's processes as descendants, and
    ``subprocess.run`` would only kill the direct child. The group is killed
    after the grace period even when the leader has already exited, because a
    descendant can ignore SIGTERM and hold no pipe open.
    """
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr,
        text=True, start_new_session=True)
    try:
        stdout, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=5)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=5)
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, err)


def resolve_module(requested: str) -> LoadedModule:
    try:
        result = run_bounded(
            ["bash", "-c", RESOLVE_SCRIPT, "neurodesk-kernel",
             ENVIRONMENT_SCRIPT, MODULE_INIT, requested],
            MODULE_TIMEOUT_SECONDS, stderr=None)
    except subprocess.TimeoutExpired:
        raise KernelError(
            f"Lmod did not answer within {MODULE_TIMEOUT_SECONDS} s for {requested!r}.") from None
    if result.returncode != 0:
        message = RESOLVE_ERRORS.get(result.returncode, RESOLVE_ERRORS[4])
        raise KernelError(message.format(init=MODULE_INIT, name=requested))
    name, path = result.stdout.split("\0", 1)
    # The loaded shell's PATH gives the precedence Lmod actually applied.
    return LoadedModule(name, tuple(dict.fromkeys(e for e in path.split(":") if e)))


def wrapper_image(path: str) -> str | None:
    """The image a transparent-singularity wrapper runs, or None."""
    try:
        with open(path, "rb") as handle:
            content = handle.read(WRAPPER_SIZE_LIMIT + 1)
    except OSError:
        return None
    if len(content) > WRAPPER_SIZE_LIMIT or not content.startswith(b"#!"):
        return None
    match = CONTAINER_EXEC.search(content.decode("utf-8", "replace"))
    return match.group(1) if match else None


def probe(wrapper: str) -> str | ProbeFailure:
    """Return the Python version if *wrapper* can run ipykernel."""
    try:
        result = run_bounded([KERNEL_WRAPPER, wrapper, "-c", PROBE_CODE],
                             PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return ProbeFailure(wrapper, f"timed out after {PROBE_TIMEOUT_SECONDS} s", False)
    lines = result.stdout.splitlines()
    version = next((line.split()[1] for line in lines
                    if re.fullmatch(r"python \d+\.\d+", line)), None)
    if result.returncode == 0 and version:
        return version
    if result.returncode == NO_IPYKERNEL_STATUS and version:
        detail = next((line[len("no-ipykernel "):] for line in lines
                       if line.startswith("no-ipykernel ")), "")
        return ProbeFailure(
            wrapper, f"Python {version} runs, but importing ipykernel failed: {detail}", True)
    detail = (result.stderr.strip().splitlines() or [f"exit status {result.returncode}"])[-1]
    return ProbeFailure(wrapper, f"could not start: {detail}", False)


def find_container_python(module: LoadedModule, commands: tuple[str, ...]) -> ContainerPython:
    failures: list[ProbeFailure] = []
    for command in commands:
        for directory in module.path:
            wrapper = os.path.join(directory, command)
            if not (os.path.isfile(wrapper) and os.access(wrapper, os.X_OK)):
                continue
            image = wrapper_image(wrapper)
            if image is None or not module.owns_image(image):
                continue
            print(f"Checking {wrapper} ...", file=sys.stderr)
            outcome = probe(wrapper)
            if isinstance(outcome, str):
                return ContainerPython(module.name, wrapper, outcome)
            failures.append(outcome)
    if not failures:
        raise KernelError(
            f"module {module.name!r} provides no {' or '.join(commands)} wrapper for its "
            f"container ({module.image_prefix}*). Use --python to name another command.")
    details = "\n".join(f"  {failure.wrapper}: {failure.reason}" for failure in failures)
    if all(failure.missing_ipykernel for failure in failures):
        raise KernelError(
            f"no Python in module {module.name!r} can run a Jupyter kernel "
            f"(ipykernel is not installed in the container):\n{details}")
    raise KernelError(
        f"could not run Python from module {module.name!r}:\n{details}")


def build_spec(python: ContainerPython) -> dict:
    return {
        "argv": [KERNEL_WRAPPER, python.wrapper, "-m", "ipykernel_launcher",
                 "-f", "{connection_file}"],
        "display_name": f"Python {python.version} ({python.module})",
        "language": "python",
        "metadata": {SPEC_MARKER: {"module": python.module, "python": python.wrapper}},
    }


def read_owned_spec(spec_dir: Path) -> dict | None:
    """Return the kernelspec in *spec_dir* if this tool wrote it."""
    try:
        spec = json.loads((spec_dir / "kernel.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(spec, dict):
        return None
    metadata = spec.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get(SPEC_MARKER), dict):
        return None
    return spec


@contextlib.contextmanager
def kernels_lock() -> Iterator[None]:
    """Serialize ownership checks and writes between concurrent invocations."""
    root = kernels_dir()
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".neurodesk-kernel.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write_spec(name: str, spec: dict) -> Path:
    spec_dir = kernels_dir() / name
    module = spec["metadata"][SPEC_MARKER]["module"]
    with kernels_lock():
        if spec_dir.exists() or spec_dir.is_symlink():
            existing = read_owned_spec(spec_dir) if not spec_dir.is_symlink() else None
            if existing is None:
                raise KernelError(
                    f"{spec_dir} already holds a kernel that neurodesk-kernel did not create.")
            owner = existing["metadata"][SPEC_MARKER].get("module")
            if owner != module:
                raise KernelError(
                    f"kernel name {name!r} is already used by module {owner!r}; "
                    f"remove it first to register {module!r}.")
        spec_dir.mkdir(parents=True, exist_ok=True)
        target = spec_dir / "kernel.json"
        fd, staging = tempfile.mkstemp(dir=spec_dir, prefix=".kernel.json.")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(json.dumps(spec, indent=1) + "\n")
            os.chmod(staging, 0o644)
            os.replace(staging, target)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(staging)
            raise
    return target


def owned_specs() -> list[tuple[str, dict]]:
    root = kernels_dir()
    if not root.is_dir():
        return []
    found = []
    for spec_dir in sorted(root.iterdir()):
        owned = spec_dir.is_dir() and not spec_dir.is_symlink()
        spec = read_owned_spec(spec_dir) if owned else None
        if spec is not None:
            found.append((spec_dir.name, spec))
    return found


def command_add(args: argparse.Namespace) -> int:
    module = resolve_module(args.module)
    commands = (args.python,) if args.python else PYTHON_COMMANDS
    python = find_container_python(module, commands)
    name = kernel_name_for(python.module)
    spec = build_spec(python)
    path = write_spec(name, spec)
    print(f"Registered kernel {name!r} ({spec['display_name']}) at {path}")
    print("Reload JupyterLab or open the kernel selector to use it.")
    return 0


def command_list(_args: argparse.Namespace) -> int:
    specs = owned_specs()
    if not specs:
        print("No container kernels registered. Add one with: neurodesk-kernel add <module>")
        return 0
    for name, spec in specs:
        info = spec["metadata"][SPEC_MARKER]
        wrapper = str(info.get("python", ""))
        state = "" if os.path.exists(wrapper) else "  [container not available]"
        print(f"{name}\t{info.get('module', '?')}\t{wrapper}{state}")
    return 0


def command_remove(args: argparse.Namespace) -> int:
    removed = []
    with kernels_lock():
        for name, spec in owned_specs():
            module = spec["metadata"][SPEC_MARKER].get("module")
            if args.kernel in (name, module):
                shutil.rmtree(kernels_dir() / name)
                removed.append(name)
    if not removed:
        raise KernelError(f"no container kernel matches {args.kernel!r}. "
                          "Run 'neurodesk-kernel list' to see registered kernels.")
    for name in removed:
        print(f"Removed kernel {name!r}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="neurodesk-kernel",
        description="Run Jupyter notebooks inside a Neurodesk tool container.")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser(
        "add", help="register a container module's Python as a kernel")
    add.add_argument("module", help="module to register, for example fsl/6.0.7.16")
    add.add_argument(
        "--python", metavar="COMMAND",
        help="Python command provided by the module "
             f"(default: try {', '.join(PYTHON_COMMANDS)})")
    add.set_defaults(handler=command_add)

    listing = commands.add_parser("list", help="list registered container kernels")
    listing.set_defaults(handler=command_list)

    remove = commands.add_parser("remove", help="remove a registered container kernel")
    remove.add_argument("kernel", help="kernel name or module, as shown by 'list'")
    remove.set_defaults(handler=command_remove)

    args = parser.parse_args(argv)
    if getattr(args, "python", None) and "/" in args.python:
        parser.error("--python takes a command name from the module, not a path")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return args.handler(args)
    except KernelError as error:
        print(f"neurodesk-kernel: error: {error}", file=sys.stderr)
    except OSError as error:
        location = f": {error.filename}" if error.filename else ""
        print(f"neurodesk-kernel: error: {error.strerror or error}{location}. "
              "Check the permissions of your Jupyter kernels directory.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
