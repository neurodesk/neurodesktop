# Testing

When making changes to the project, add tests for new functionality, build the
container, and run the tests inside the container under `/opt/tests/` to ensure
that changes do not break existing functionality.

```bash
pytest /opt/tests/
```

## Negative Test Convention

When adding tests for pipeline or module-loading workflows, always include a
negative test alongside the positive happy-path test. The negative test should
use `module load funny-name-tool`, which is a non-existent module, and assert
that the workflow fails with a non-zero exit code and does not produce output.

This guards against silent failures caused by `set +euo pipefail` and `|| true`
patterns in workflow scripts.

## Building the Container

Build the Docker image locally:

```bash
docker build . -t neurodesktop:latest
```

Build and run using the convenience script:

```bash
./build_and_run.sh
```

The [`build_and_run.sh`](../build_and_run.sh) script builds the image and runs it
with recommended settings, including persistent home, CVMFS enabled, and port
8888.

## Modes of `build_and_run.sh`

The script always builds the image first, then dispatches based on the first
argument:

- `./build_and_run.sh` — Launch the container interactively with the classic
  Docker settings (privileged, root, CVMFS enabled).
- `./build_and_run.sh test` — Build, start a single container with the default
  configuration, and run `pytest /opt/tests/` inside. Tears down the container
  afterwards.
- `./build_and_run.sh hpc [user] [uid] [gid]` — Launch an **interactive**
  session that simulates an Apptainer HPC deployment: no `--privileged`, no
  `--user=root`, no sudo, a non-`jovyan` container user (default `sciget`, UID
  `5000`), host-owned bind-mount over `/home/jovyan`, and `APPTAINER_CONTAINER=1`.
  Jupyter is exposed on `127.0.0.1:8888`. Use this to reproduce HPC-only bugs
  locally.
- `./build_and_run.sh hpctest [user] [uid] [gid]` — Same HPC simulation
  envelope as `hpc`, but runs detached and executes `pytest /opt/tests/`
  inside. Tears down the container and removes the temp `/etc/passwd` /
  `/etc/group` / home files on exit.
- `./build_and_run.sh fulltest` — Runs the test suite across **five
  configurations in parallel** and dumps each container's captured log once
  they have *all* finished: the four `std` configs (`CVMFS_DISABLE ∈ {false,
  true}` × `GRANT_SUDO ∈ {no, yes}`) plus the `hpc` Apptainer simulation
  (`sciget`, UID `5000`, no root). Fastest wall-clock path — roughly one
  container-start's worth of time regardless of how many configs you add —
  but you get no live progress, only the final summary + per-config logs.
  Exits non-zero if any configuration fails.
- `./build_and_run.sh fulltest_verbose` — Same set of five configurations,
  but runs them **sequentially** and streams each container's pytest output
  to your terminal live. Much slower (≈5× the `fulltest` wall-clock) but you
  can watch per-test progress, see failures in real time, and abort early
  with Ctrl-C. Each config is torn down before the next one starts. A
  summary table is printed at the end listing PASS/FAIL for each config.

### HPC simulation details

The `hpc` and `hpctest` modes, and the `hpc` leg of `fulltest`, share a common
launch envelope that mirrors what Apptainer does on shared HPC nodes:

- `--user <uid>:<gid>` with a non-1000 UID (so `jovyan`-specific paths are
  exercised against a different real user).
- A generated `/etc/passwd` and `/etc/group` bind-mounted read-only, adding the
  simulated user alongside `jovyan` so tools like `id`, `vncserver`, and `sshd`
  resolve the UID.
- A temporary host directory bind-mounted over `/home/jovyan` so the container
  starts with an empty home the HPC user can populate.
- `APPTAINER_CONTAINER=1` and `APPTAINER_NAME` exported so every
  `is_apptainer_runtime()` check branches into its unprivileged path.
- `CVMFS_DISABLE=true` because CVMFS needs FUSE and capabilities that the
  simulated unprivileged environment does not grant.

After `hpc` or `hpctest`, tear everything down with:

```bash
docker rm -f neurodesktop-hpc   # or neurodesktop-hpctest
rm -rf /tmp/neurodesktop-hpc-home.* /tmp/neurodesktop-hpc-passwd.* /tmp/neurodesktop-hpc-group.*
```
