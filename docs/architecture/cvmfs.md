---
title: CVMFS and Neurocommand
description: CVMFS server selection and mount configuration, and the
  neurocommand CLI/module system for neuroimaging tools
parent: ../architecture.md
status: current
last-reviewed: "2026-07-31"
---

# CVMFS and Neurocommand

Part of [Architecture](../architecture.md). Related environment variables are
listed in
[Environment variables](../environment-variables.md#cvmfs-and-modules).

## CVMFS

CVMFS, the CernVM File System, distributes neuroimaging software containers
without local storage. Server selection is handled by
[`config/jupyter/cvmfs_server_select.sh`](../../config/jupyter/cvmfs_server_select.sh):
it probes a pool of direct Stratum-1 servers and Cloudflare-fronted CDN
endpoints in parallel for reachability, measures cold-cache download
throughput on the lowest-latency finalists, and writes `CVMFS_SERVER_URL` with
the fastest server first and the runners-up as fallbacks (plus a non-CDN host
if the top picks are all on the same CDN). Every probe carries a unique
cache-busting query string so CDN edge caches cannot inflate the measurement —
real workloads fetch long-tail objects that are cold at the edge. The CVMFS
client walks the list in order and abandons a degraded server at runtime via
the failover settings (`CVMFS_LOW_SPEED_LIMIT`, `CVMFS_TIMEOUT`,
`CVMFS_MAX_RETRIES`, `CVMFS_HOST_RESET_AFTER`) in
[`config/cvmfs/default.local`](../../config/cvmfs/default.local). A successful
ranking is cached in `~/.cache/neurodesktop/cvmfs-selection.env` for seven days
and reused while its primary server passes a health check; a failed mount
triggers a forced re-probe. Eager Docker startup runs the selector as root, so
after writing this cache it restores ownership of the cache path to the
remapped notebook UID/GID; otherwise Jupyter cannot create its own sibling
cache directories.

Configuration lives in [`config/cvmfs/`](../../config/cvmfs/). CVMFS can be
disabled with `CVMFS_DISABLE=true`. The Dockerfile pins both the CVMFS client
package and the repository bootstrap package; the bootstrap download is also
verified by SHA-256 so the `latest` URL cannot silently change a reproducible
build.

## Build-time CVMFS setup

The active repository configuration is generated at startup by
`cvmfs_server_select.sh` (see above). The image bakes in
[`config/cvmfs/neurodesk.ardc.edu.au.conf`](../../config/cvmfs/neurodesk.ardc.edu.au.conf)
as a static default so mounts that happen before the selection ran still work;
CI jobs that configure CVMFS on the build host copy the same file.

## Neurocommand

Neurocommand is cloned from
[`neurodesk/neurocommand`](https://github.com/neurodesk/neurocommand) during the
build. It provides the CLI and module system for neuroimaging tools, uses Lmod
for module management, and stores containers in
`/neurodesktop-storage/containers`.
