#!/usr/bin/env python3
"""Run frozen base tests against a patched checkout, then candidate tests.

The frozen suite protects against a candidate ``conftest.py`` or pytest config
silencing existing tests. It is an integrity check, not a general security
proof: tested candidate Python can still run inside the pytest interpreter.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys


BASELINE_BOOTSTRAP = r"""
import importlib.util
from pathlib import Path
import sys

baseline = Path(sys.argv[1])
workspace = Path(sys.argv[2])
testlib_path = baseline / "tests" / "testlib.py"
spec = importlib.util.spec_from_file_location("testlib", testlib_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Cannot load frozen testlib: {testlib_path}")
testlib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(testlib)
# Base tests keep their helpers, but exercise exactly the submitted source.
testlib.REPO_ROOT = workspace
sys.modules["testlib"] = testlib
import pytest
raise SystemExit(pytest.main(sys.argv[3:]))
"""


def run(label, argv, *, cwd, env=None):
    # Let the worker's bounded command collector consume test output as it is
    # produced. Retaining a malicious test's output here could exhaust memory.
    print(f"=== {label} ===", flush=True)
    return subprocess.run(argv, cwd=cwd, env=env, stderr=subprocess.STDOUT).returncode


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    baseline = args.baseline.resolve()
    workspace = args.workspace.resolve()
    tests = baseline / "tests"
    if not (tests / "testlib.py").is_file() or not (tests / "unit").is_dir():
        raise SystemExit("Frozen baseline is missing tests/testlib.py or tests/unit")

    # -I and /dev/null prevent candidate paths and pytest configuration from
    # affecting this process. The arguments below are the trusted baseline
    # runner configuration; the snapshot supplies only test sources.
    baseline_env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        baseline_env.pop(name, None)
    baseline_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    base_status = run(
        "frozen baseline against patched workspace",
        [
            sys.executable, "-I", "-c", BASELINE_BOOTSTRAP,
            str(baseline), str(workspace),
            "-c", os.devnull,
            "--rootdir", str(tests),
            "--confcutdir", str(tests),
            "-p", "no:cacheprovider",
            str(tests / "unit"), "-q",
        ],
        cwd=baseline,
        env=baseline_env,
    )
    candidate_status = run(
        "candidate tests",
        [sys.executable, "-m", "pytest", "tests/unit", "-q"],
        cwd=workspace,
    )
    return 0 if base_status == 0 and candidate_status == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
