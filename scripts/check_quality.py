#!/usr/bin/env python3
"""Run the repository's quality gates with already installed tools."""

import argparse
from collections.abc import Callable, Sequence
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
PYTHON_FORMAT_SCOPE = (
    "config/agents/nbi_mirror_opencode.py",
    "config/agents/opencode_update_config.py",
    "config/agents/provider_security.py",
    "extensions/t3-code-server/neurodesk_t3_code/__init__.py",
    "extensions/t3-code-server/neurodesk_t3_code/naming.py",
    "extensions/t3-code-server/neurodesk_t3_code/serverext.py",
    "extensions/t3-code-server/neurodesk_t3_code/supervisor.py",
    "scripts/check_quality.py",
)
# check_quality.sh runs this runner with the python3 on PATH, which is 3.10 on
# the Ubuntu 22.04 runner host. mypy.ini targets the image's Python 3.12, so
# these modules get their own strict check; ruff.toml sets their Ruff target.
RUNNER_HOST_PYTHON_VERSION = "3.10"
RUNNER_HOST_TYPED_SCOPE = ("scripts/check_quality.py",)
SHELL_FORMAT_SCOPE = (
    "scripts/check_quality.sh",
    "scripts/install_quality_tools.sh",
    "config/jupyter/guard_ollama_host.sh",
    "config/jupyter/detect_resource_limits.sh",
    "config/jupyter/host_slurm_environment.sh",
)
JAVASCRIPT_EXCLUDED = (
    # Frozen excerpts of the pinned upstream wheel; patch tests own them.
    "tests/fixtures/widget-manager/",
    # TypeScript output and the generated JupyterLab bundle.
    "extensions/neurodesk-launcher/lib/",
    "extensions/neurodesk-launcher/neurodesk_launcher/labextension/",
)
JAVASCRIPT_LINT = "tools/javascript-lint"
ESLINT = f"{JAVASCRIPT_LINT}/node_modules/eslint/bin/eslint.js"
SHELL_SHEBANG = re.compile(rb"^#![^\n]*\b(?:bash|sh|dash|ksh)\b")


def repository_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return sorted({os.fsdecode(name) for name in result.stdout.split(b"\0") if name})


def shell_files() -> list[str]:
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


def javascript_files() -> list[str]:
    """Return the single owned-JavaScript scope for syntax and lint checks."""
    return [
        name
        for name in repository_files()
        if Path(name).suffix in {".js", ".cjs", ".mjs"}
        and not name.startswith(JAVASCRIPT_EXCLUDED)
        and "node_modules" not in Path(name).parts
        and (ROOT / name).is_file()
    ]


def run(
    command: Sequence[str], *, cwd: Path = ROOT, source: bytes | None = None
) -> bool:
    """Run one tool and report whether it passed; a missing tool fails."""
    print("+ " + " ".join(command), flush=True)
    if shutil.which(command[0]) is None:
        print(
            f"Missing {command[0]}; run scripts/install_quality_tools.sh "
            "and activate the printed tool paths.",
            file=sys.stderr,
        )
        return False
    return subprocess.run(command, cwd=cwd, input=source, check=False).returncode == 0


def lint_javascript(files: Sequence[str]) -> bool:
    if not (ROOT / ESLINT).is_file():
        print(
            f"Missing ESLint; run npm ci --prefix {JAVASCRIPT_LINT} "
            "--no-audit --no-fund.",
            file=sys.stderr,
        )
        return False
    return run(
        [
            "node",
            ESLINT,
            "--config",
            f"{JAVASCRIPT_LINT}/eslint.config.mjs",
            "--max-warnings",
            "0",
            *files,
        ]
    )


# Each gate runs all of its commands and returns one pass flag per command.
def check_python() -> list[bool]:
    return [
        run(["ruff", "check", "."]),
        run(["mypy", "--config-file", "mypy.ini"]),
        run(
            [
                "mypy",
                "--config-file",
                "mypy.ini",
                "--python-version",
                RUNNER_HOST_PYTHON_VERSION,
                *RUNNER_HOST_TYPED_SCOPE,
            ]
        ),
    ]


def check_shell() -> list[bool]:
    return [run(["shellcheck", *shell_files()])]


def check_javascript() -> list[bool]:
    files = javascript_files()
    results = []
    for name in files:
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
    if files:
        results.append(lint_javascript(files))
    return results


def check_frontend() -> list[bool]:
    cwd = ROOT / "extensions/neurodesk-launcher"
    return [
        run(["npm", "run", "typecheck"], cwd=cwd),
        run(["npm", "run", "lint"], cwd=cwd),
    ]


def check_format() -> list[bool]:
    return [
        run(["ruff", "format", "--check", *PYTHON_FORMAT_SCOPE]),
        run(["shfmt", "-d", *SHELL_FORMAT_SCOPE]),
    ]


def check_workflow() -> list[bool]:
    return [run(["actionlint"])]


def check_docker() -> list[bool]:
    return [run(["docker", "buildx", "build", "--check", "."])]


GATES: dict[str, Callable[[], list[bool]]] = {
    "python": check_python,
    "shell": check_shell,
    "javascript": check_javascript,
    "frontend": check_frontend,
    "format": check_format,
    "workflow": check_workflow,
    "docker": check_docker,
}


def gate_passed(gate: str) -> bool:
    print(f"=== {gate} ===", flush=True)
    try:
        return all(GATES[gate]())
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"{gate} failed: {error}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gate", choices=(*GATES, "all"), nargs="?", default="all")
    requested: str = parser.parse_args().gate
    gates = list(GATES) if requested == "all" else [requested]
    # A list, not a generator: every selected gate runs after a failure.
    outcomes = [gate_passed(gate) for gate in gates]
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
