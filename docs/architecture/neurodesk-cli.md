---
title: Neurodesk command
description: The `neurodesk` dispatcher for the Neurodesktop helper commands,
  its built-in check, test and info commands, and the older command names
parent: ../architecture.md
status: current
last-reviewed: "2026-11-13"
---

# Neurodesk command

`neurodesk` is the single command-line entry point for the Neurodesktop helper
tools. [`config/jupyter/neurodesk_cli.py`](../../config/jupyter/neurodesk_cli.py)
is installed at `/usr/local/bin/neurodesk`, owned by root and run by the
image's system Python in isolated mode.

## Commands

`neurodesk --help` lists every command, grouped as below. The `COMMANDS` table
in the dispatcher is the only list; the help screen is generated from it.

| Command | Runs | Purpose |
| --- | --- | --- |
| `neurodesk cite` | `neurodesk-cite` | Write `CITATIONS.md` ([tool citations](citations.md)) |
| `neurodesk kernel` | `neurodesk-kernel` | Register a container module's Python as a kernel ([container kernels](cvmfs.md)) |
| `neurodesk uv` | `neurodesk-uv` | Project uv environments ([uv environments](uv-environments.md)) |
| `neurodesk jobs` | `neurodesk-jobs` | Remote SSH/Slurm jobs ([remote jobs](remote-jobs.md)) |
| `neurodesk update` | `neurodesk-update` | Update the Neurodesk application menu ([CVMFS and Neurocommand](cvmfs.md)) |
| `neurodesk check` | built in | Read-only CVMFS, GPU, Slurm and T3 checks |
| `neurodesk test` | built in | Run the installed container test suite |
| `neurodesk info` | built in | Image version, CPU and memory limits, storage, GPU, CVMFS server |
| `neurodesk t3` | `t3_neurodesk_setup` | T3 Code remote access ([T3 Code](t3-code.md)) |
| `neurodesk agent preflight` | `neurodesk-agent-preflight` | Agent environment check ([coding agents](coding-agents.md)) |
| `neurodesk astra` | `neurodesk-astra-provenance` | ASTRA output publication and provenance checks ([ASTRA](astra.md)) |
| `neurodesk lightcone materialize` | `neurodesk-lc-materialize` | Lightcone materialization outside `sbatch` ([ASTRA](astra.md)) |

A dispatched command replaces the dispatcher process with
`/usr/local/bin/<helper>` and passes the remaining arguments through
unchanged, so `neurodesk cite --help` prints the helper's own help. Each helper
names itself as its `neurodesk` subcommand in usage text. The dispatcher never
consults `PATH` to find a helper, and it does not change any helper's
interpreter, ownership or privilege handling.

`neurodesk help COMMAND` is the same as `neurodesk COMMAND --help`. An unknown
command exits with status 2 and suggests the closest name. A group word on its
own, such as `neurodesk agent`, names the commands in that group.

## Older names

Every helper is still installed under its original name. Existing callers
depend on those paths: uv kernelspecs record `/usr/local/bin/neurodesk-uv`,
the menu's `update.sh` runs `/usr/local/bin/neurodesk-update`, and released
agent skills call `neurodesk-astra-provenance`. User documentation uses the
`neurodesk` forms. The installed agent guidance,
[`config/agents/AGENTS.md`](../../config/agents/AGENTS.md), keeps the older
agent command names so that it matches the released agent skills. The older names print no deprecation warning,
because kernels and scripts would show it on every launch.

## Built-in commands

`neurodesk check [cvmfs|gpu|slurm|t3 ...]` runs every check when no name is
given. Each check prints `PASS`, `FAIL` or `SKIP`. The command exits with
status 1 when any check fails; a skipped check does not fail.

- `cvmfs` lists `neurodesk-modules` and reads the `user.host` extended
  attribute of `/cvmfs/neurodesk.ardc.edu.au` in a child process with a
  timeout, because a hung FUSE mount would otherwise block the caller. It
  skips when `CVMFS_DISABLE` is `true`.
- `gpu` runs `neurodesktop-gpu-check` when `DISPLAY` is set. Otherwise it
  lists devices with `nvidia-smi`, and skips when no NVIDIA driver is visible.
- `slurm` lists partitions with `sinfo` and skips when Slurm is not installed.
- `t3` runs `t3_neurodesk_setup --check`. It skips when the user's Tailscale
  socket does not exist, which means remote access was never set up.

`neurodesk test [PYTEST_ARGS ...]` runs the full container tier in `/opt/tests`
with the image's conda Python. Extra arguments go to pytest. The pytest cache
is disabled because the installed suite is root-owned.

`neurodesk info` prints `NEURODESKTOP_VERSION`, the user, the cgroup CPU and
memory limits, free space in `/neurodesktop-storage`, GPUs reported by
`nvidia-smi`, and the active CVMFS server.

## Tests

`pytest tests/unit/test_neurodesk_cli.py` runs the dispatcher, including its
`execv`, against stand-in helpers, a fake CVMFS repository and fake `sinfo`.
In a built image, `pytest /opt/tests/test_neurodesk_cli_image.py` checks that
each dispatched command's help names the new command, that the older names
still run, and that `check cvmfs` agrees with the startup mode.
