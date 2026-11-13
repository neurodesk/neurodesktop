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
    parser = argparse.ArgumentParser(
        prog="neurodesk jobs", description="Neurodesk SSH/Slurm jobs (site validation required)")
    parser.add_argument("--profile", type=Path, help="site profile JSON (required except for restore)")
    parser.add_argument("--state", type=Path,
                        default=Path.home() / ".local/state/neurodesk-jobs",
                        help="local job state directory (default: %(default)s)")
    # A metavar keeps the private _rpc and _worker entry points out of usage;
    # subparsers added without help stay out of the command list.
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("connect", help="Open authenticated SSH connections to the site")
    commands.add_parser("doctor", help="Check the site's login and transfer tools")
    prepare = commands.add_parser("prepare", help="Freeze a saved DataLad dataset and Apptainer SIF")
    prepare.add_argument("--dataset", required=True, type=Path)
    prepare.add_argument("--image", required=True, type=Path)
    prepare.add_argument("--request", required=True, type=Path)
    for name, summary in (
        ("stage", "Transfer a prepared run's inputs to the site store"),
        ("submit", "Submit a staged run to Slurm"),
        ("status", "Show a run's Slurm and result state"),
        ("cancel", "Cancel a submitted run"),
        ("collect", "Copy a finished run's results back"),
        ("archive", "Write a verified, self-contained archive of a collected run"),
    ):
        commands.add_parser(name, help=summary).add_argument("run_id")
    logs = commands.add_parser("logs", help="Last 64 KiB per Slurm or attempt log")
    logs.add_argument("run_id")
    logs.add_argument("--attempt")
    recover = commands.add_parser("restore", help="Restore an archived run without the cluster")
    recover.add_argument("archive", type=Path)
    recover.add_argument("destination", type=Path)
    rpc = commands.add_parser("_rpc")
    rpc.add_argument("operation", choices=OPERATIONS)
    batch = commands.add_parser("_worker")
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
