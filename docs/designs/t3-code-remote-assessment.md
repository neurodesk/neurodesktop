---
title: T3 Code remote desktop assessment
description: Requirements for running T3 Code inside Neurodesktop and connecting
  from the T3 desktop app
parent: index.md
status: implemented
last-reviewed: "2026-09-10"
---

# T3 Code remote desktop assessment

Part of [Design records](index.md). See
[T3 Code remote access](../architecture/t3-code.md) for current behavior.
The original opt-in startup described here was replaced by automatic startup
with Jupyter on 2026-09-16.

## Feasibility

T3 supports a headless server through `t3 serve`. Its desktop app can connect
through direct pairing, desktop-managed SSH, or T3 Connect. T3 Connect supports
a command-line host and avoids router forwarding. The server must remain
running inside Neurodesktop so agent work uses its files, tools, and environment.
See the upstream [remote-access guide](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md).

Neurodesktop already installs Node.js 24 and the coding-agent CLIs in the
[Dockerfile](../../Dockerfile). The standard launcher persists the user's home
in [build_and_run.sh](../../build_and_run.sh). These provide most prerequisites.

## Implemented integration

- Install an exact tested version of the `t3` npm package in the pinned-tool
  section of the Dockerfile. Published `t3` is `0.0.40` at this snapshot and
  requires Node `^22.16 || ^23.11 || >=24.10`, per
  [npm package metadata](https://registry.npmjs.org/t3/latest).
  Check the installed Node minor version and both image architectures.
  Keep the desktop and server versions compatible.
- A Jupyter `ExtensionApp` owns the optional T3 process group as the actual
  notebook user. It handles startup, bounded restart, and shutdown without
  systemd or a persisted PID file.
- Preserve T3 state and provider authentication under the persistent user home.
  T3 defaults to `~/.t3`; `T3CODE_HOME` or `--base-dir` overrides that base,
  per the [CLI configuration](https://github.com/pingdotgg/t3code/blob/main/apps/server/src/cli/config.ts)
  and [home resolution](https://github.com/pingdotgg/t3code/blob/main/apps/server/src/os-jank.ts).
  Authentication must be available inside the container.
- Configure T3's Codex provider to use `/usr/bin/codex` directly. The existing
  [interactive wrapper](../../config/agents/codex) prints status lines and
  changes defaults before launching the binary. Verify the real provider
  handshake instead of assuming this wrapper is safe for protocol use.
  T3 supports this override through Settings → Providers → Binary path in its
  [provider setup](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md#providers).
- Preserve the workspace instructions normally seeded by that wrapper when a
  user adds a project, without replacing an existing `AGENTS.md`.

Use container lifecycle management for the server. Upstream's Linux
`t3 service install` requires systemd user services, which the Neurodesktop
[startup flow](../architecture.md#container-initialization-flow) does not provide.
See [T3 background services](https://github.com/pingdotgg/t3code/blob/main/docs/user/background-service.md).

## Connection choices

T3 Connect is the first option to evaluate for a convenient desktop connection
without extra published ports. It requires account setup and connectivity to
the relay. A private connection can instead pair through an SSH tunnel or a
reachable private endpoint.

For Docker, a direct endpoint needs an explicit published port and a server
bind address reachable through Docker networking. For Apptainer on HPC, use an
available compute-node port and tunnel to that node. The existing
[Sherlock launcher](../../scripts/connectSherlock.sh) forwards the notebook
port; an independent T3 endpoint needs additional forwarding.

Desktop-managed SSH starts T3 on its SSH target. Pointing it at a login host
does not enter a container. Neurodesktop's existing SSH daemon also starts on
demand with the graphical desktop, per
[jupyterlab_startup.sh](../../config/jupyter/jupyterlab_startup.sh), so it is
not an always-available T3 entry point.

## Synthesis decision

Two designs were compared. A standalone lifecycle command exposed start,
stop, status, pairing, and connection operations. The selected design makes
Jupyter the process owner and leaves pairing and T3 Connect on the upstream
`t3` CLI. This gives the process one lifetime owner and removes PID records,
locks, and a second access-control command.

The standalone design contributed strict uid and home ownership checks, quiet
provider launchers for all three bundled agents, non-blocking startup, and
credential-leak assertions. Its custom pairing command was rejected because
T3 0.0.40 does not provide that interface.

The implementation initially selected `t3 start` from the second design.
Runtime validation showed that this command requires the desktop service
launcher IPC channel. The sidecar therefore uses `t3 serve`, whose output is
discarded so its startup credential cannot enter Neurodesktop logs.

## Acceptance checks

The checkout suite covers configuration, process ownership, opt-in behavior,
the locked image install, native-payload cleanup, and Docker publication. The
built-image suite starts the installed server and exercises its native PTY.
A release check still pairs the matching desktop client, starts a provider
thread, and verifies reconnection against a persistent home after restart.
