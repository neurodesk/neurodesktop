from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class ModuleProfile:
    modules: tuple[str, ...]

    def identity(self) -> bytes:
        return json.dumps(
            {"execution": "modules", "modules": self.modules},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


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
