---
title: CVMFS and Neurocommand
description: CVMFS server selection and mount configuration, and the
  neurocommand CLI/module system for neuroimaging tools
parent: ../architecture.md
status: current
last-reviewed: "2026-09-29"
---

# CVMFS and Neurocommand

Part of [Architecture](../architecture.md). Related environment variables are
listed in
[Environment variables](../environment-variables.md#cvmfs-and-modules).

## CVMFS

CVMFS, the CernVM File System, distributes neuroimaging software containers
without local storage. Server selection is handled by
[`config/jupyter/cvmfs_server_select.sh`](../../config/jupyter/cvmfs_server_select.sh):
the shell entry point runs
[`cvmfs_server_select.py`](../../config/jupyter/cvmfs_server_select.py), which
uses Python's standard library and curl without requiring a mounted repository.

Selection proceeds in three stages:

1. Probe manifests in parallel, including the FNAL CDN endpoint. Collapse direct
   aliases with the same observed IP, scheme, and port, while preserving separate
   `openhtc.io` hostnames because shared CDN edge IPs can route to different origins.
2. Download the same hash-verified root catalog sequentially from **every**
   distinct reachable destination. Prefer the most widely advertised catalog
   hash, rather than trusting a claimed revision number. If a mirror cannot serve
   that snapshot, test its own verified catalog and objects so an untrusted or
   lagging source cannot exclude healthy alternatives. Manifest latency does not
   determine the shortlist.
3. Test the five fastest catalog responders with two immutable data objects
   discovered from the catalog. Select the smallest and largest eligible file
   chunks, with distinct hashes when sizes tie; inspect
   at most three catalogs and supplement with the root catalog if chunks are
   unavailable. Rank by total verified bytes divided by total transfer time.
   A failed, truncated, or corrupt transfer disqualifies the finalist. Fill
   missing fallback slots from the remaining measured destinations.

Each request has a unique query string to avoid reusing a CDN edge response.
This does not flush origin caches. All HTTP probes use direct connections,
matching the image's `CVMFS_HTTP_PROXY=DIRECT` configuration. Downloads must
finish successfully with HTTP 200 and match the catalog's content hash.
These hashes detect corrupted transfers; the CVMFS client still verifies
repository signatures when mounting. Requests, object size, catalog expansion,
and the entire 180-second benchmark have bounds. Catalog inspection runs in an
isolated Python child with a clean environment, a four-second deadline, CPU and
file-size limits, and a 256 MiB Linux address-space limit. Root startup drops the
child to `nobody` with no supplementary groups before Python executes. Catalog
inspection failure falls back to catalog-object transfers without parsing those
bytes in the parent. An incomplete ranking is not cached. No verified finalist produces the static GeoAPI fallback and exit 1.

The fastest four verified destinations become `CVMFS_SERVER_URL`. If the
shortlist is entirely CDN endpoints, also test the fastest screened direct
endpoint and retain it as a fallback if its data checks succeed. With
`CVMFS_USE_GEOAPI=no`, the client walks this order. Runtime failover settings
(`CVMFS_LOW_SPEED_LIMIT`, `CVMFS_TIMEOUT`, `CVMFS_MAX_RETRIES`,
`CVMFS_HOST_RESET_AFTER`) remain in
[`config/cvmfs/default.local`](../../config/cvmfs/default.local).

Rankings are cached in `~/.cache/neurodesktop/cvmfs-selection.env` for one day.
At startup, the primary must complete both hash-verified samples at at least half
its recorded speed. The first fallback competes on the same objects; a fallback
more than 20% faster triggers a full re-ranking. The primary must also serve a
valid manifest. Old cache formats, changed candidate pools, expired caches, and
failed checks trigger a new benchmark. Cache contents are parsed as data, never
executed as shell commands. Config and cache writes are atomic. Eager startup
restores notebook ownership of the home cache path after root writes it.

These checks select mirrors at startup, not continuously while a mount is in
use. A failed mount forces a re-probe. Runtime failover only detects a server
that crosses the client's failure thresholds; it does not continuously compare
healthy mirrors. Use `cvmfs_server_select.sh --force-probe` to bypass the cache.
A benchmark measures this client's current network path and cannot guarantee
that its winner will remain fastest for every object or future workload.

Configuration lives in [`config/cvmfs/`](../../config/cvmfs/). CVMFS can be
disabled with `CVMFS_DISABLE=true`. The Dockerfile pins both the CVMFS client
package and the repository bootstrap package; the bootstrap download is also
verified by SHA-256 so the `latest` URL cannot silently change a reproducible
build.

The scheduled [CVMFS health workflow](../../.github/workflows/test-cvmfs.yml)
mounts each advertised endpoint and compares it with neurocommand's desired
container inventory. Both its single-server and fallback-list jobs use
[`check_cvmfs_inventory.sh`](../../.github/workflows/check_cvmfs_inventory.sh),
which fails unavailable or empty inventory downloads and reports every missing
container in the snapshot before failing. When the first comparison finds a
mismatch, the checker synchronously asks the mounted client to remount the
latest catalog and repeats the complete comparison once against the same
downloaded inventory. The refresh has a 45-second timeout and a five-second
kill grace period, uses noninteractive sudo when needed, and records catalog
status before and after. A refresh failure or timeout still checks and reports
all missing entries. This lets a stale mount catch up without hiding
persistent replica omissions.

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
