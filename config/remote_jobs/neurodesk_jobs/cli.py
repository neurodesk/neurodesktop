"""Public CLI and private cluster-helper entry points."""

import argparse
import json
import os
from pathlib import Path
import sys

from .client import Client, client_lock, connect, restore, run_site, saved_site
from .core import JobError, read
from .remote import OPERATIONS, get, put, worker


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(
        prog="neurodesk jobs",
        description="Run a containerized analysis on a Slurm cluster over SSH (experimental). "
                    "Start with: neurodesk jobs connect HOST")
    parser.add_argument("--state", type=Path,
                        default=Path.home() / ".local/state/neurodesk-jobs",
                        help="local job records (default: %(default)s)")
    # A metavar keeps the private entry points out of usage; subparsers added
    # without help stay out of the command list.
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    link = commands.add_parser("connect", help="Log in to a cluster (an alias from ~/.ssh/config)")
    link.add_argument("host", nargs="?", help="SSH alias; omit to reconnect to the saved cluster")
    link.add_argument("--data-root", help="cluster directory for staged data (default: scratch)")
    commands.add_parser("doctor", help="Check the connected cluster's login-node tools")
    prepare = commands.add_parser("prepare", help="Freeze a saved DataLad dataset and Apptainer SIF")
    prepare.add_argument("--dataset", required=True, type=Path)
    prepare.add_argument("--image", required=True, type=Path)
    prepare.add_argument("--request", required=True, type=Path)
    for name, summary in (
        ("stage", "Upload a prepared run's inputs (submit does this automatically)"),
        ("submit", "Upload a prepared run if needed and submit it to Slurm"),
        ("status", "Show a run's Slurm and result state"),
        ("cancel", "Cancel a submitted run"),
        ("collect", "Download a finished run's verified results"),
    ):
        commands.add_parser(name, help=summary).add_argument("run_id")
    archive = commands.add_parser("archive", help="Write a verified, self-contained archive of a collected run")
    archive.add_argument("run_id")
    archive.add_argument("--to", type=Path, help="archive directory (default: STATE/archives)")
    logs = commands.add_parser("logs", help="Last 64 KiB per Slurm or attempt log")
    logs.add_argument("run_id")
    logs.add_argument("--attempt")
    recover = commands.add_parser("restore", help="Restore an archived run without the cluster")
    recover.add_argument("archive", type=Path)
    recover.add_argument("destination", type=Path)
    rpc = commands.add_parser("_rpc")
    rpc.add_argument("operation", choices=OPERATIONS)
    upload = commands.add_parser("_put")
    upload.add_argument("data_root")
    upload.add_argument("sha")
    upload.add_argument("size", type=int)
    commands.add_parser("_get")
    batch = commands.add_parser("_worker")
    batch.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "_rpc":
            result = OPERATIONS[args.operation](json.load(sys.stdin))
        elif args.command == "_put":
            put(args.data_root, args.sha, args.size)
            return
        elif args.command == "_get":
            get(json.load(sys.stdin))
            return
        elif args.command == "_worker":
            sys.exit(worker(args.directory))
        elif args.command == "restore":
            result = restore(args.archive, args.destination)
        else:
            state = args.state.expanduser().resolve()
            with client_lock(state):
                if args.command == "connect":
                    result = connect(state, args.host, args.data_root)
                elif args.command in {"doctor", "prepare"}:
                    client = Client(saved_site(state), state)
                    if args.command == "doctor":
                        helper = client.transport.install(client.site)
                        result = client.transport.rpc(client.site, helper, "doctor",
                                                      {"site": client.site})
                    else:
                        record = client.prepare(args.dataset, args.image, read(args.request))
                        result = {key: record[key] for key in ("run_id", "state", "request_digest")}
                else:
                    client = Client(run_site(state, args.run_id), state)
                    if args.command == "logs":
                        result = client.logs(args.run_id, args.attempt)
                    elif args.command == "archive":
                        result = client.archive(args.run_id, args.to)
                    else:
                        result = getattr(client, args.command)(args.run_id)
        print(json.dumps(result, indent=2, sort_keys=True))
    except KeyError as exc:
        print(f"neurodesk jobs: a job record or cluster reply is missing the field {exc}",
              file=sys.stderr)
        sys.exit(1)
    except (JobError, OSError, ValueError) as exc:
        print(f"neurodesk jobs: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
