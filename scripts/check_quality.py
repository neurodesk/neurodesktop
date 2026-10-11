#!/usr/bin/env python3
"""Run the repository's quality gates with already installed tools."""

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
PYTHON_FORMAT_SCOPE = (
    "config/agents/provider_security.py",
    "extensions/t3-code-server/neurodesk_t3_code/naming.py",
)
SHELL_FORMAT_SCOPE = (
    "scripts/check_quality.sh",
    "scripts/install_quality_tools.sh",
    "config/jupyter/guard_ollama_host.sh",
)
GATES = ("python", "shell", "javascript", "frontend", "format", "workflow", "docker")
SHELL_SHEBANG = re.compile(rb"^#![^\n]*\b(?:bash|sh|dash|ksh)\b")


def repository_files():
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return sorted({os.fsdecode(name) for name in result.stdout.split(b"\0") if name})


def shell_files():
    selected = []
    for name in repository_files():
        path = ROOT / name
        if not path.is_file():
            continue
        if path.suffix == ".sh":
            selected.append(name)
        else:
            with path.open("rb") as stream:
                if SHELL_SHEBANG.match(stream.readline(256)):
                    selected.append(name)
    return selected


def javascript_files():
    return [
        name
        for name in repository_files()
        if Path(name).suffix in {".js", ".cjs", ".mjs"}
        and not name.startswith("tests/fixtures/widget-manager/")
        and not any(
            part in {"lib", "vendor", "node_modules", "labextension"}
            for part in Path(name).parts
        )
        and (ROOT / name).is_file()
    ]


def run(command, *, cwd=ROOT, source=None):
    print("+ " + " ".join(command), flush=True)
    if shutil.which(command[0]) is None:
        print(
            f"Missing {command[0]}; run scripts/install_quality_tools.sh "
            "and activate the printed tool paths.",
            file=sys.stderr,
        )
        return 1
    return subprocess.run(command, cwd=cwd, input=source, check=False).returncode


def check_gate(gate):
    if gate == "python":
        results = [
            run(["ruff", "check", "."]),
            run(["mypy", "--config-file", "mypy.ini"]),
        ]
    elif gate == "shell":
        results = [run(["shellcheck", *shell_files()])]
    elif gate == "javascript":
        results = []
        for name in javascript_files():
            print(f"Checking JavaScript: {name}", flush=True)
            if Path(name).suffix == ".cjs":
                results.append(run(["node", "--check", name]))
            else:
                results.append(
                    run(
                        ["node", "--input-type=module", "--check"],
                        source=(ROOT / name).read_bytes(),
                    )
                )
    elif gate == "frontend":
        cwd = ROOT / "extensions/neurodesk-launcher"
        results = [
            run(["npm", "run", "typecheck"], cwd=cwd),
            run(["npm", "run", "lint"], cwd=cwd),
        ]
    elif gate == "format":
        results = [
            run(["ruff", "format", "--check", *PYTHON_FORMAT_SCOPE]),
            run(["shfmt", "-d", *SHELL_FORMAT_SCOPE]),
        ]
    elif gate == "workflow":
        results = [run(["actionlint"])]
    elif gate == "docker":
        results = [run(["docker", "buildx", "build", "--check", "."])]
    else:
        raise ValueError(f"Unknown quality gate: {gate}")
    return int(any(results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gate", choices=(*GATES, "all"), nargs="?", default="all")
    args = parser.parse_args()
    gates = GATES if args.gate == "all" else (args.gate,)
    results = []
    for gate in gates:
        print(f"=== {gate} ===", flush=True)
        try:
            results.append(check_gate(gate))
        except (OSError, subprocess.CalledProcessError) as error:
            print(f"{gate} failed: {error}", file=sys.stderr)
            results.append(1)
    return int(any(results))


if __name__ == "__main__":
    raise SystemExit(main())
