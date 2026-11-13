#!/opt/conda/bin/python3 -I
"""Manage opt-in project-local uv notebook kernels."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

MARKER = "neurodesk_uv_prototype"
WRAPPER = "/opt/neurodesktop/kernel_wrapper.sh"


def clean_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
            and not key.startswith("CONDA_")}


def kernel_name(project: Path) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", project.name.lower()).strip("-") or "project"
    digest = hashlib.sha256(os.fsencode(project)).hexdigest()[:12]
    return f"neurodesk-uv-{slug[:40]}-{digest}"


def atomic_json(path: Path, data: dict) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".uv-kernel-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_marker(project: Path) -> dict:
    for directory in (project, project / ".venv"):
        marker = directory / f".{MARKER}.json"
        if marker.is_file():
            data = json.loads(marker.read_text())
            if not isinstance(data, dict) or not isinstance(data.get("project"), str):
                raise ValueError(f"Invalid prototype ownership record: {marker}")
            return data
    return {}


def is_uv_environment(venv: Path) -> bool:
    config = venv / "pyvenv.cfg"
    return config.is_file() and bool(re.search(r"^uv\s*=", config.read_text(), re.MULTILINE))


def launch(project: Path, arguments: list[str]) -> None:
    venv = project / ".venv"
    if read_marker(project).get("project") != str(project) or not (venv / "bin/python").is_file():
        raise ValueError(f"Environment missing or moved. Run: {sys.executable} "
                         f"{Path(__file__).resolve()} rebuild {str(project)!r}")
    env = clean_environment()
    env["VIRTUAL_ENV"] = str(venv)
    env["PATH"] = str(venv / "bin") + os.pathsep + env.get("PATH", "")
    env["PYTHONNOUSERSITE"] = "1"
    os.execve(venv / "bin/python", [str(venv / "bin/python"), "-m",
                                  "ipykernel_launcher", *arguments], env)


def prepare(project: Path, command: str, wrapper: Path) -> None:
    from jupyter_core.paths import jupyter_data_dir

    if os.geteuid() == 0:
        raise ValueError("Run as the notebook user, not root.")
    if project in {Path("/"), Path.home().resolve()} or os.path.ismount(project):
        raise ValueError("Choose an analysis subfolder, not a home or mount root.")
    if command != "remove" and not project.is_dir():
        raise ValueError("Create the analysis folder first.")
    root = Path(jupyter_data_dir()) / "kernels"
    root.mkdir(parents=True, exist_ok=True)
    # Serialize the entire operation, including environment deletion and registration.
    with (root / f".{MARKER}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        spec_dir = root / kernel_name(project)
        target = spec_dir / "kernel.json"
        if spec_dir.is_symlink():
            raise ValueError("Refusing a symlinked kernelspec directory.")
        registered = False
        if command == "remove" and not target.is_file():
            raise ValueError(f"No prototype registration for {project}")
        if spec_dir.exists():
            if not target.is_file():
                raise ValueError(f"Incomplete kernel registration at {spec_dir}; leaving it untouched.")
            existing = json.loads(target.read_text())
            if existing.get("metadata", {}).get(MARKER) != {"project": str(project)}:
                raise ValueError("Refusing to replace a kernel owned by another tool.")
            registered = True
        if command == "remove":
            target.unlink()
            spec_dir.rmdir()
            print("Removed registration; project files and environment retained.")
            return
        if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
            raise ValueError(f"Kernel wrapper unavailable: {wrapper}")
        for parent in project.parents:
            manifest = parent / "pyproject.toml"
            if manifest.is_file():
                data = tomllib.loads(manifest.read_text())
                if "workspace" in data.get("tool", {}).get("uv", {}):
                    raise ValueError("Prototype does not support folders inside uv workspaces.")
        manifest = project / "pyproject.toml"
        data = tomllib.loads(manifest.read_text()) if manifest.exists() else {}
        if "workspace" in data.get("tool", {}).get("uv", {}):
            raise ValueError("Prototype does not support uv workspaces.")
        venv = project / ".venv"
        if venv.is_symlink():
            raise ValueError("Refusing a symlinked .venv.")
        marker = read_marker(project)
        if venv.exists() and (not (marker or registered) or not is_uv_environment(venv)):
            raise ValueError("Existing .venv is unmanaged; use a fresh analysis folder.")
        if marker and marker.get("project") != str(project) and command != "rebuild":
            raise ValueError("Project moved; run rebuild after shutting down its kernels.")
        uv = shutil.which("uv")
        if not uv:
            raise ValueError("uv is required.")
        env = clean_environment()
        env["UV_PROJECT_ENVIRONMENT"] = str(venv)
        env["UV_LINK_MODE"] = "copy"

        def run(*arguments: str) -> None:
            location = "--directory" if arguments[0] == "init" else "--project"
            subprocess.run([uv, *arguments, location, str(project)],
                           cwd=project, env=env, check=True)

        if command == "rebuild":
            if not manifest.is_file() or not (project / "uv.lock").is_file():
                raise ValueError("Rebuild requires pyproject.toml and uv.lock; run init first.")
            if venv.exists():
                shutil.rmtree(venv)
        if not manifest.exists():
            run("init", "--bare", "--no-workspace", "--no-pin-python", "--vcs", "none",
                "--name", kernel_name(project))
        # uv may recreate .venv during add/sync, so ownership lives beside it.
        if not venv.exists():
            run("venv", str(venv))
        atomic_json(project / f".{MARKER}.json", {"project": str(project)})
        if command == "init":
            deps = data.get("dependency-groups", {}).get("dev", [])
            if not any(isinstance(dep, str) and re.match(r"(?i)^ipykernel(?:$|[<>=!~;\[ @])", dep)
                       for dep in deps):
                run("add", "--dev", "ipykernel==6.31.0", "--no-sync")
            if not (project / "uv.lock").is_file():
                run("lock")
        run("sync", "--locked", "--group", "dev")
        subprocess.run([str(venv / "bin/python"), "-I", "-c", "import ipykernel"],
                       env=env, check=True)
        spec_dir.mkdir(exist_ok=True)
        atomic_json(target, {
            "argv": [str(wrapper), sys.executable, "-I", str(Path(__file__).resolve()),
                     "launch", str(project), "-f", "{connection_file}"],
            "display_name": f"Python (uv prototype: {project.name})", "language": "python",
            "metadata": {MARKER: {"project": str(project)}},
        })
        print(f"Registered {kernel_name(project)}. Select Python (uv prototype: {project.name}).")


def main() -> int:
    parser = argparse.ArgumentParser(prog="neurodesk uv", description=__doc__)
    # `launch` is the kernelspec's entry point, not a user command.
    parser.add_argument("command", choices=["init", "rebuild", "remove", "launch"],
                        metavar="{init,rebuild,remove}",
                        help="create, rebuild after a move or Python change, or unregister")
    parser.add_argument("directory", type=Path, help="the uv project directory")
    parser.add_argument("--kernel-wrapper", type=Path, default=Path(WRAPPER),
                        help=argparse.SUPPRESS)
    args, rest = parser.parse_known_args()
    try:
        project = args.directory.expanduser().resolve()
        if args.command == "launch":
            launch(project, rest)
        else:
            if rest:
                parser.error(f"unexpected arguments: {rest}")
            prepare(project, args.command, args.kernel_wrapper.resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"uv kernel prototype: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
