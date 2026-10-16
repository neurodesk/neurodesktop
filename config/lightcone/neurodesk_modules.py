from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class ModuleProfile:
    modules: tuple[str, ...]

    def identity(self) -> bytes:
        return json.dumps(
            {
                "execution": "modules",
                "modules": self.modules,
                "engine": engine_identity(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


def engine_identity() -> str:
    """Bind module runs to the installed adapter and the engine it adapts."""
    package = Path(__file__).parent
    digest = hashlib.sha256(
        f"{platform.python_implementation()}:{platform.python_version()}".encode()
    )
    for path in sorted(package.rglob("*.py")):
        name = path.relative_to(package).as_posix().encode()
        content = path.read_bytes()
        for value in (name, content):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return f"sha256:{digest.hexdigest()}"


def rerun_command(target: str) -> str:
    bootstrap = """import sys
try:
    from lightcone.neurodesk_modules import engine_identity
except ImportError:
    sys.exit("Required Neurodesk Lightcone adapter is absent; use the original Neurodesktop image")
if engine_identity() != sys.argv[1]:
    sys.exit("Neurodesk Lightcone engine identity mismatch; use the original Neurodesktop image")
from lightcone.engine.worker import main
sys.exit(main(sys.argv[2:]))
"""
    python = shlex.quote(sys.executable)
    command = shlex.join(
        [sys.executable, "-I", "-c", bootstrap, engine_identity(), target]
    )
    return (
        f"if ! test -x {python}; then echo 'Required Neurodesk Lightcone runtime is absent; "
        "use the original Neurodesktop image' >&2; exit 127; fi; " + command
    )


def profile(root: Path, *, committed: bool = False) -> ModuleProfile | None:
    path = root / "pyproject.toml"
    data = tomllib.loads(path.read_text())
    tool = data.get("tool", {})
    declaration = tool.get("neurodesk", {}).get("lightcone")
    if declaration is None:
        return None
    from lightcone.engine.project import ProjectError

    if (
        not isinstance(declaration, dict)
        or set(declaration) != {"execution", "modules"}
        or declaration["execution"] != "modules"
    ):
        raise ProjectError(
            "[tool.neurodesk.lightcone] requires execution='modules' and modules"
        )
    modules = declaration["modules"]
    if (
        not isinstance(modules, list)
        or not modules
        or any(
            not isinstance(item, str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.+-]*/[0-9][A-Za-z0-9_.+-]*", item
            )
            for item in modules
        )
        or len(set(modules)) != len(modules)
    ):
        raise ProjectError(
            "modules must be unique exact tool/version pins, e.g. fsl/6.0.7.22"
        )
    if "image" in tool.get("lightcone", {}):
        raise ProjectError(
            "module execution cannot also declare [tool.lightcone.image]"
        )
    if committed:
        saved = subprocess.run(
            ["git", "show", "HEAD:pyproject.toml"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if saved.returncode or saved.stdout != path.read_bytes():
            raise ProjectError(
                "commit pyproject.toml before executing a module profile"
            )
    return ModuleProfile(tuple(modules))


def module_environment(declared: ModuleProfile, root: Path) -> dict[str, str]:
    """Reconstruct modules on the worker, never trust the driver's loaded PATH."""
    from lightcone.engine.project import ProjectError

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"BASH_ENV", "ENV", "NEURODESKTOP_PREVIOUS_BASH_ENV"}
        and not key.startswith("BASH_FUNC_")
    }
    shell = """set -e
if [ -r /opt/neurodesktop/agent_bash_env.sh ]; then
    source /opt/neurodesktop/agent_bash_env.sh >&2
else
    source /etc/profile.d/lmod.sh >&2
fi
module --force purge >&2
module load "$@" >&2
unset BASH_ENV ENV NEURODESKTOP_PREVIOUS_BASH_ENV
/usr/bin/env -0
"""
    result = subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            shell,
            "neurodesk-lightcone",
            *declared.modules,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise ProjectError(
            f"cannot load declared modules: {result.stderr.decode(errors='replace')}"
        )
    loaded = dict(
        item.decode().split("=", 1) for item in result.stdout.split(b"\0") if item
    )
    actual = set(loaded.get("LOADEDMODULES", "").split(":"))
    if not set(declared.modules) <= actual:
        raise ProjectError("worker did not load every declared module pin")
    return loaded


def backend(runtime):
    from lightcone.engine.sandbox.boundary import Unavailable
    from lightcone.engine.sandbox.model import Attestation, Capability

    declared = profile(runtime.root, committed=True)
    if declared is None:
        return None

    @dataclass(frozen=True)
    class ModuleAttestation(Attestation):
        modules: tuple[str, ...] = declared.modules

    @dataclass(frozen=True)
    class ModuleBackend(Unavailable):
        def attest(self, policy):
            return ModuleAttestation(mechanism="none", fs="open")

    return ModuleBackend(
        capability=Capability(
            kind="none",
            detail="Neurodesk external module profile: " + ", ".join(declared.modules),
        )
    )


def policy_for(runtime, ordinary):
    try:
        declared = profile(runtime.root, committed=True)
        if declared is None:
            return ordinary
        loaded = module_environment(declared, runtime.root)
    except Exception:
        import shutil

        shutil.rmtree(ordinary.tmp_home, ignore_errors=True)
        raise
    return replace(
        ordinary,
        env={
            **loaded,
            **ordinary.env,
            "PATH": os.pathsep.join([str(runtime.env_dir / "bin"), loaded["PATH"]]),
            "BASH_ENV": "",
            "ENV": "",
            "NEURODESKTOP_PREVIOUS_BASH_ENV": "",
            "APPTAINER_HOME": str(ordinary.tmp_home),
            "SINGULARITY_HOME": str(ordinary.tmp_home),
        },
    )
