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
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

KERNEL_WRAPPER = "/opt/neurodesktop/kernel_wrapper.sh"
ENVIRONMENT_SCRIPT = "/opt/neurodesktop/environment_variables.sh"
MODULE_INIT = "/usr/share/module.sh"
PYTHON_COMMANDS = ("python3", "python")
# The first run of a CVMFS container fetches its files on demand.
PROBE_TIMEOUT_SECONDS = 600
SPEC_MARKER = "neurodesk_container"
KERNEL_PREFIX = "neurodesk-"

PROBE_CODE = (
    "import sys, ipykernel_launcher; "
    "print('%d.%d' % sys.version_info[:2])"
)

RESOLVE_SCRIPT = r"""
source "$1" >/dev/null 2>&1
source "$2" >/dev/null 2>&1 || exit 3
before=$PATH
module load "$3" >&2 || exit 4
printf '%s\0%s\0%s\0' "${LOADEDMODULES:-}" "$before" "$PATH"
"""


class KernelError(Exception):
    """A user-facing failure, reported without a traceback."""


@dataclass(frozen=True)
class LoadedModule:
    name: str
    path_entries: tuple[str, ...]


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


def resolve_module(requested: str) -> LoadedModule:
    result = subprocess.run(
        ["bash", "-c", RESOLVE_SCRIPT, "neurodesk-kernel",
         ENVIRONMENT_SCRIPT, MODULE_INIT, requested],
        stdout=subprocess.PIPE, check=False,
    )
    if result.returncode == 3:
        raise KernelError(f"Lmod is unavailable: could not source {MODULE_INIT}.")
    if result.returncode != 0:
        raise KernelError(
            f"could not load module {requested!r}. "
            "Run 'ml av' to list available modules.")
    loaded, before, after = result.stdout.decode().split("\0")[:3]

    tool = requested.split("/", 1)[0]
    matches = [m for m in loaded.split(":") if m.split("/", 1)[0] == tool]
    if not matches:
        raise KernelError(f"module {requested!r} loaded but is not listed as loaded.")
    previous = set(before.split(":"))
    added = tuple(entry for entry in after.split(":")
                  if entry and entry not in previous)
    if not added:
        raise KernelError(f"module {matches[-1]!r} adds no command directory to PATH.")
    return LoadedModule(matches[-1], added)


def probe(wrapper: str) -> tuple[str | None, str]:
    """Return the Python version if *wrapper* can run ipykernel, else the reason."""
    try:
        result = subprocess.run(
            [KERNEL_WRAPPER, wrapper, "-c", PROBE_CODE],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL, check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {PROBE_TIMEOUT_SECONDS} s"
    version = result.stdout.strip().splitlines()[-1:] if result.returncode == 0 else []
    if version and re.fullmatch(r"\d+\.\d+", version[0]):
        return version[0], ""
    detail = (result.stderr.strip().splitlines() or [f"exit status {result.returncode}"])[-1]
    return None, detail


def find_container_python(module: LoadedModule, commands: tuple[str, ...]) -> ContainerPython:
    attempts = []
    for command in commands:
        for directory in module.path_entries:
            wrapper = os.path.join(directory, command)
            if not (os.path.isfile(wrapper) and os.access(wrapper, os.X_OK)):
                continue
            print(f"Checking {wrapper} ...", file=sys.stderr)
            version, reason = probe(wrapper)
            if version:
                return ContainerPython(module.name, wrapper, version)
            attempts.append(f"  {wrapper}: {reason}")
    if not attempts:
        raise KernelError(
            f"module {module.name!r} provides none of: {', '.join(commands)}.")
    raise KernelError(
        f"no Python in module {module.name!r} can run a Jupyter kernel "
        "(ipykernel is not installed in the container):\n" + "\n".join(attempts))


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


def write_spec(name: str, spec: dict) -> Path:
    spec_dir = kernels_dir() / name
    if spec_dir.exists() and read_owned_spec(spec_dir) is None:
        raise KernelError(
            f"{spec_dir} already holds a kernel that neurodesk-kernel did not create.")
    spec_dir.mkdir(parents=True, exist_ok=True)
    target = spec_dir / "kernel.json"
    staging = spec_dir / "kernel.json.tmp"
    staging.write_text(json.dumps(spec, indent=1) + "\n")
    staging.replace(target)
    return target


def owned_specs() -> list[tuple[str, dict]]:
    root = kernels_dir()
    if not root.is_dir():
        return []
    found = []
    for spec_dir in sorted(root.iterdir()):
        spec = read_owned_spec(spec_dir) if spec_dir.is_dir() else None
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
    candidates = {args.kernel, kernel_name_for(args.kernel)}
    removed = []
    for name, spec in owned_specs():
        module = spec["metadata"][SPEC_MARKER].get("module")
        if name in candidates or module == args.kernel:
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
        return 1


if __name__ == "__main__":
    sys.exit(main())
