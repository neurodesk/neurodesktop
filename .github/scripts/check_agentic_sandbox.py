"""Probe the actual worker image with dummy credentials; never calls a model."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid


spec = importlib.util.spec_from_file_location("worker", Path(__file__).with_name("agentic_worker.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)

PROBE = r'''
from pathlib import Path
import json, socket, sys
assert sys.stdin.read() == "stdin-reaches-container"
for name in ("/codex/auth.json", "/proc/1/environ", "/output/secret"):
    try:
        Path(name).read_text()
    except (OSError, PermissionError):
        pass
    else:
        raise AssertionError("Sandbox read a forbidden path: " + name)
Path("/workspace/probe.txt").write_text("allowed")
for name in ("/workspace/.git/config", "/control/probe.txt"):
    try:
        Path(name).write_text("forbidden")
    except (OSError, PermissionError):
        pass
    else:
        raise AssertionError("Sandbox wrote a forbidden path: " + name)
try:
    socket.create_connection(("1.1.1.1", 443), timeout=2)
except OSError:
    pass
else:
    raise AssertionError("Sandbox allowed tool network access")
print(json.dumps({"sandbox": "passed", "stdin": "passed"}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sudo", action="store_true", help="Use sudo -n for Docker")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="neurodesktop-sandbox-", dir=os.environ.get("RUNNER_TEMP")) as temporary:
        root = Path(temporary)
        workspace, control, output, auth = (root / name for name in ("workspace", "control", "output", "auth"))
        for path in (workspace, control, output, auth, workspace / ".git"):
            path.mkdir()
        (auth / "auth.json").write_text('{"dummy": "not-a-credential"}')
        (output / "secret").write_text("dummy-output")
        (workspace / ".git/config").write_text("dummy-git")
        name = f"neurodesktop-sandbox-{uuid.uuid4().hex}"
        command = worker.docker_args(name, workspace, control, output, auth=auth)
        command += worker.subscription_guard() + ["codex", "sandbox", "-P", "worker", *worker.permission_args(), "-C", "/workspace", "python", "-c", PROBE]
        prefix = ["sudo", "-n"] if args.sudo else []
        try:
            completed = subprocess.run(prefix + command, input="stdin-reaches-container", text=True, capture_output=True, timeout=60)
        finally:
            subprocess.run(prefix + ["docker", "rm", "-f", name], capture_output=True, timeout=30)
        if completed.returncode:
            raise SystemExit(completed.stderr or completed.stdout)
        assert json.loads(completed.stdout) == {"sandbox": "passed", "stdin": "passed"}
        assert (workspace / "probe.txt").read_text() == "allowed"
        print("Worker sandbox passed: stdin, credential denial, protected writes, tool network denial.")


if __name__ == "__main__":
    main()
