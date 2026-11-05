"""Public CLI and private JSON RPC/worker entry points."""

import argparse
import json
import os
from pathlib import Path
import sys

from .client import Client, client_lock, restore
from .core import JobError, read
from .remote import OPERATIONS, worker


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Neurodesk SSH/Slurm jobs (site validation required)")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--state", type=Path,
                        default=Path.home() / ".local/state/neurodesk-jobs")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("connect", "doctor"):
        commands.add_parser(name)
    prepare = commands.add_parser("prepare", help="Freeze a saved DataLad dataset and Apptainer SIF")
    prepare.add_argument("--dataset", required=True, type=Path)
    prepare.add_argument("--image", required=True, type=Path)
    prepare.add_argument("--request", required=True, type=Path)
    for name in ("stage", "submit", "status", "cancel", "collect", "archive"):
        commands.add_parser(name).add_argument("run_id")
    logs = commands.add_parser("logs", help="Last 64 KiB per Slurm or attempt log")
    logs.add_argument("run_id")
    logs.add_argument("--attempt")
    recover = commands.add_parser("restore")
    recover.add_argument("archive", type=Path)
    recover.add_argument("destination", type=Path)
    rpc = commands.add_parser("_rpc", help=argparse.SUPPRESS)
    rpc.add_argument("operation", choices=OPERATIONS)
    batch = commands.add_parser("_worker", help=argparse.SUPPRESS)
    batch.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "_rpc":
            result = OPERATIONS[args.operation](json.load(sys.stdin))
        elif args.command == "_worker":
            sys.exit(worker(args.directory))
        elif args.command == "restore":
            result = restore(args.archive, args.destination)
        else:
            if not args.profile:
                parser.error("--profile is required")
            client = Client(read(args.profile), args.state)
            with client_lock(client.state):
                if args.command == "connect":
                    client.transport.connect()
                    result = {"state": "authenticated", "passwords_stored": False}
                elif args.command == "doctor":
                    helper = client.transport.install()
                    result = client.transport.rpc(helper, "doctor", {"site": client.site})
                    result["transfer_tools"] = {
                        argv[0]: client.transport.command(client.site["transfer_host"], argv)
                        for argv in (["git", "--version"], ["git-annex", "version", "--raw"])}
                elif args.command == "prepare":
                    record = client.prepare(args.dataset, args.image, read(args.request))
                    result = {key: record[key] for key in ("run_id", "state", "request_digest")}
                elif args.command == "logs":
                    result = client.logs(args.run_id, args.attempt)
                else:
                    result = getattr(client, args.command)(args.run_id)
        print(json.dumps(result, indent=2, sort_keys=True))
    except (JobError, OSError, ValueError, KeyError) as exc:
        print(f"neurodesk-jobs: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
