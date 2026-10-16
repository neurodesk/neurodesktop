"""Own one native Lightcone allocation and release precisely its returned ID."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


def materialize(args) -> int:
    from lightcone import neurodesk_modules
    from lightcone.engine import dataset, project
    from lightcone.engine.compute.model import Identity

    root = Path(args.project).resolve(strict=True)
    if os.environ.get("SLURM_JOB_ID"):
        raise project.ProjectError("run outside sbatch; Lightcone owns the allocation")
    for name in ("astra.yaml", "pyproject.toml", "uv.lock", ".python-version"):
        if not (root / name).is_file():
            raise project.ProjectError(
                f"missing {name}; prepare the project with lc init first"
            )
    declared = neurodesk_modules.profile(root, committed=True)
    if declared is None:
        raise project.ProjectError(
            "declare [tool.neurodesk.lightcone] for this module helper"
        )
    if not project.license_of(root):
        raise project.ProjectError(
            "declare [project].license to maintain RO-Crate execution evidence"
        )
    dataset.require_committer(root)
    if dataset.status(root):
        raise project.ProjectError("commit author changes before materializing")
    for name in ("status.json", "manifest.json", "run-manifest.json"):
        if (root / name).exists():
            raise project.ProjectError(
                f"remove competing evidence {name}; Lightcone maintains ro-crate-metadata.json"
            )
    env = {**os.environ, **neurodesk_modules.module_environment(declared, root)}
    env.pop("BASH_ENV", None)
    env.pop("NEURODESKTOP_PREVIOUS_BASH_ENV", None)
    if (
        "LC_COMPUTE_CONFIG" not in env
        and not (Path.home() / ".lightcone/compute.yaml").exists()
    ):
        env["LC_COMPUTE_CONFIG"] = "/opt/neurodesktop/lightcone-compute.yaml"
    lc = str(Path(sys.executable).parent / "lc")

    def run(*argv, capture=False):
        return subprocess.run(
            [lc, *argv],
            cwd=root,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else None,
        )

    # Separate acceptance from readiness so interruption during wait still owns the ID.
    launched = run(
        "compute",
        "launch",
        "--cpus",
        str(args.cpus),
        "--memory",
        str(args.memory),
        "--time",
        args.time,
        "--json",
        capture=True,
    )
    payload = json.loads(launched.stdout)
    full_id = payload.get("id")
    if not full_id:
        print(launched.stdout, file=sys.stderr)
        return launched.returncode or 1
    Identity.decode(full_id)
    result = 1
    try:
        if launched.returncode:
            print(launched.stdout, file=sys.stderr)
            return launched.returncode
        ready = run(
            "compute",
            "status",
            full_id,
            "--wait",
            "--timeout",
            str(args.timeout),
            "--json",
        )
        if ready.returncode:
            return ready.returncode
        result = run(
            "materialize",
            full_id,
            *(["--refresh"] if args.refresh else []),
            *args.targets,
        ).returncode
    finally:
        stopped = run("compute", "down", full_id, "--json")
        if stopped.returncode:
            print(f"could not release owned cluster {full_id}", file=sys.stderr)
            result = result or stopped.returncode
    return result


def main() -> int:
    from lightcone.engine.project import ProjectError

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", help="universe/output or universe")
    parser.add_argument("--project", default=".")
    parser.add_argument("--cpus", type=int, default=2)
    parser.add_argument("--memory", type=float, default=8)
    parser.add_argument("--time", default="2h")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--refresh", action="store_true")
    try:
        return materialize(parser.parse_args())
    except (ProjectError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "interrupted; inspect lc compute status before retrying an interrupted submission",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    sys.exit(main())
