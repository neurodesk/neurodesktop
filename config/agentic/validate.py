#!/usr/bin/env python3
"""Run frozen base tests against a patched checkout, then candidate tests.

The frozen suite protects against a candidate ``conftest.py`` or pytest config
silencing existing tests. It is an integrity check, not a general security
proof: tested candidate Python can still run inside the pytest interpreter.
"""

import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time


LINT_NODE_MODULES = Path("/opt/javascript-lint/node_modules")
MAX_STAGE_OUTPUT = 8 * 1024 * 1024
STAGE_TAIL_BYTES = 8 * 1024
STAGE_TIMEOUT = 840


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


def prepare_checkout_dependencies(workspace):
    if not LINT_NODE_MODULES.is_dir():
        return
    lint = workspace / "tools" / "javascript-lint"
    for path in (workspace / "tools", lint):
        if path.is_symlink():
            raise ValueError(f"Unsafe checkout dependency path: {path}")
    if not lint.is_dir():
        return
    target = lint / "node_modules"
    if target.is_symlink():
        if target.readlink() != LINT_NODE_MODULES:
            raise ValueError(f"Unexpected checkout dependency path: {target}")
    elif target.exists():
        raise ValueError(f"Unexpected checkout dependency path: {target}")
    prefix = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    relative = "tools/javascript-lint/node_modules"
    tracked = subprocess.run([*prefix, "ls-files", "--error-unmatch", "--", relative],
                             cwd=workspace, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    ignored = subprocess.run([*prefix, "check-ignore", "--no-index", "-q", "--", relative],
                             cwd=workspace, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    if tracked.returncode != 1 or ignored.returncode != 0:
        raise ValueError("Checkout dependency path must be untracked and ignored")
    if not target.is_symlink():
        target.symlink_to(LINT_NODE_MODULES)


def run(label, argv, *, cwd, env=None, timeout=STAGE_TIMEOUT):
    started = time.monotonic()
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True)
    tail = bytearray()
    total = 0
    status = None
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector, process.stdout:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map() or process.poll() is None:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                status = "timed_out"
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                tail.extend(chunk)
                del tail[:-STAGE_TAIL_BYTES]
                if total > MAX_STAGE_OUTPUT:
                    status = "output_limit"
                    break
            if status is not None:
                break
        if status is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)
    output = tail.decode(errors="replace").encode()[-STAGE_TAIL_BYTES:].decode(errors="ignore")
    return {
        "stage": label,
        "status": status or ("passed" if process.returncode == 0 else "failed"),
        "returncode": process.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "output_tail": output,
        "output_truncated": total > STAGE_TAIL_BYTES or status == "output_limit",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--prepare-workspace", type=Path)
    args = parser.parse_args(argv)
    if args.prepare_workspace is not None:
        prepare_checkout_dependencies(args.prepare_workspace.resolve())
        return 0
    if args.baseline is None or args.workspace is None:
        parser.error("--baseline and --workspace are required for validation")
    baseline = args.baseline.resolve()
    workspace = args.workspace.resolve()
    tests = baseline / "tests"
    if not (tests / "testlib.py").is_file() or not (tests / "unit").is_dir():
        raise SystemExit("Frozen baseline is missing tests/testlib.py or tests/unit")
    prepare_checkout_dependencies(workspace)

    # -I and /dev/null prevent candidate paths and pytest configuration from
    # affecting this process. The arguments below are the trusted baseline
    # runner configuration; the snapshot supplies only test sources.
    baseline_env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        baseline_env.pop(name, None)
    baseline_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    print("=== frozen baseline against patched workspace ===", flush=True)
    base_status = run(
        "frozen_baseline",
        [
            sys.executable, "-I", "-c", BASELINE_BOOTSTRAP,
            str(baseline), str(workspace),
            "-c", os.devnull,
            "--rootdir", str(tests),
            "--confcutdir", str(tests),
            "-p", "no:cacheprovider",
            str(tests / "unit"), "-q",
        ],
        cwd=workspace,
        env=baseline_env,
    )
    print("=== candidate tests ===", flush=True)
    candidate_status = run(
        "candidate",
        [sys.executable, "-m", "pytest", "tests/unit", "-q"],
        cwd=workspace,
    )
    results = [base_status, candidate_status]
    for result in results:
        print(f"=== {result['stage']} output ===", flush=True)
        print(result["output_tail"], flush=True)
    summary = [{key: value for key, value in result.items() if key != "output_tail"} for result in results]
    print("VALIDATION_SUMMARY " + json.dumps(summary), flush=True)
    return 0 if all(result["status"] == "passed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
