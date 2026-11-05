#!/usr/bin/env python3
"""Run a synthetic job through the installed CLI against an explicitly chosen site.

Requires an authenticated SSH master, a configured profile, and an Apptainer
SIF containing /bin/sh and awk (for example, BusyBox). Never deletes site data.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    root = args.workspace.resolve()
    root.mkdir(mode=0o700)  # Existing directories are never reused or removed.
    env = dict(os.environ, GIT_AUTHOR_NAME="Neurodesk smoke test",
               GIT_AUTHOR_EMAIL="test@localhost", GIT_COMMITTER_NAME="Neurodesk smoke test",
               GIT_COMMITTER_EMAIL="test@localhost")

    def execute(argv, cwd=root, environment=env):
        result = subprocess.run([str(arg) for arg in argv], cwd=cwd, env=environment,
                                capture_output=True, text=True, timeout=args.timeout,
                                check=False)
        if result.returncode:
            raise RuntimeError(f"{argv[0]} failed:\n{result.stdout}\n{result.stderr}")
        return result.stdout

    def cli(action, *arguments):
        reply = json.loads(execute(["neurodesk-jobs", "--profile", args.profile.resolve(),
                                    "--state", root / "client", action, *arguments]))
        print(json.dumps({"action": action, "result": reply}), flush=True)
        return reply

    source = root / "source"
    execute(["datalad", "create", source])
    execute(["datalad", "create", "-d", source, source / "nested"])
    (source / "nested/numbers.txt").write_text("1\n2\n3\n")
    (source / "nested/binary.bin").write_bytes(bytes(range(256)) * 4)
    (source / "analyze.sh").write_text(
        '#!/bin/sh\nset -eu\ntest "$1" = "argument with spaces"\ntest "$2" = \'{"key":1}\'\n'
        'test "$(wc -c < /inputs/nested/binary.bin)" -eq 1024\n'
        "awk '{s+=$1;n++} END {printf \"%.6f\\n\",s/n}' "
        "/inputs/nested/numbers.txt > /outputs/mean.txt\n")
    (source / "analyze.sh").chmod(0o755)
    (source / "validate.sh").write_text('set -eu\ntest "$(cat /outputs/mean.txt)" = "2.000000"\n')
    execute(["datalad", "save", "-r", "-d", source, "-m", "Synthetic inputs"])
    specification = {"inputs": ["nested/numbers.txt", "nested/binary.bin", "analyze.sh", "validate.sh"],
                     "command": ["/inputs/analyze.sh", "argument with spaces", '{"key":1}'],
                     "validate": ["sh", "/inputs/validate.sh"], "outputs": ["mean.txt"]}
    request = root / "request.json"
    request.write_text(json.dumps(specification))
    prepared = cli("prepare", "--dataset", source, "--image", args.image.resolve(), "--request", request)
    run_id = prepared["run_id"]
    cli("stage", run_id)
    accepted = cli("submit", run_id)
    assert accepted["state"] == "accepted", accepted
    assert cli("submit", run_id)["job_id"] == accepted["job_id"]
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        state = cli("status", run_id)
        if state["scheduler"]["state"] == "COMPLETED":
            break
        if state["scheduler"]["state"] in {"FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY"}:
            cli("logs", run_id)
            raise RuntimeError("Synthetic job failed; inspect the run and attempt logs")
        time.sleep(5)
    else:
        raise RuntimeError(f"Timed out observing {run_id}; the job was not automatically cancelled")
    collection = cli("collect", run_id)
    assert (Path(collection["path"]) / "output/mean.txt").read_text() == "2.000000\n"
    archive = cli("archive", run_id)
    offline = dict(env, GIT_SSH_COMMAND="/bin/false", GIT_ALLOW_PROTOCOL="file")
    execute(["neurodesk-jobs", "restore", archive["path"], root / "restored"], environment=offline)
    assert (root / "restored/output/mean.txt").read_text() == "2.000000\n"
    print(json.dumps({"verified": True, "run_id": run_id, "archive": archive}), flush=True)


if __name__ == "__main__":
    main()
