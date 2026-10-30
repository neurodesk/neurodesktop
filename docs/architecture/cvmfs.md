---
title: CVMFS and Neurocommand
description: CVMFS server selection and mount configuration, and the
  neurocommand CLI/module system for neuroimaging tools
parent: ../architecture.md
status: current
last-reviewed: "2026-10-23"
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

1. Probe manifests in parallel, including the FNAL Stratum 1 both directly and
   through its CDN endpoint. Geo-DNS names such as `cvmfs-geoproximity` resolve
   to FNAL only some of the time, so FNAL is also listed by name. Collapse direct
   aliases with the same observed IP, scheme, and port, while preserving separate
   `openhtc.io` hostnames because shared CDN edge IPs can route to different origins.
   When aliases collapse, keep a hostname that is its own DNS target over a
   CNAME such as `cvmfs.neurodesk.org`. A geo-DNS alias can resolve to a
   different host at mount time and duplicate another configured entry.
2. Download the same hash-verified root catalog sequentially from **every**
   distinct reachable destination. Prefer the most widely advertised catalog
   hash, rather than trusting a claimed revision number. If a mirror cannot serve
   that snapshot, test its own verified catalog and objects so an untrusted or
   lagging source cannot exclude healthy alternatives. Shortlist by the workload
   estimate below, using the manifest and catalog request latencies and the
   catalog's transfer rate.
3. Test the five best-estimated responders with two immutable data objects
   discovered from the catalog, then three further small manifest requests.
   Select the smallest and largest eligible file chunks, with distinct hashes
   when sizes tie; inspect at most three catalogs and supplement with the root
   catalog if chunks are unavailable. A failed, truncated, or corrupt transfer
   disqualifies the finalist. Fill missing fallback slots from the remaining
   measured destinations.

Mirrors are ranked by the estimated time of a reference workload: 1,000
sequential requests at the median measured request latency, plus 256 MiB at the
measured bulk transfer rate. Module listing and tool startup each issue hundreds
of small-object requests, so request latency can matter more than bandwidth; a
throughput-only ranking favors a distant high-bandwidth mirror. Request latency
is the time from request to first byte. Bulk rate is the data objects' bytes
divided by their body-transfer time. Both exclude DNS, TCP, and TLS setup,
because the CVMFS client reuses connections. The weights are fixed and apply
equally to every region; the measured network path decides the order.

The candidate pool includes the Toronto Stratum 1 at
`http://cvmfs-toronto.neurodesk.org`. Clients in Toronto select it when its
measured workload estimate is lowest. It also appears in the image's static
GeoAPI fallback and the daily CVMFS health-check matrix. Adding it to the pool
invalidates cached rankings from earlier images at the next startup.

Each request has a unique query string to avoid reusing a CDN edge response.
This does not flush origin caches. CDN endpoints are therefore scored on their
cold path. The client's local CVMFS cache already serves the user's own repeat
reads. Edge hits depend on other Neurodesk users behind the same CDN location,
which varies by region and which a repeated probe would only measure after
warming the edge itself. All HTTP probes use direct connections,
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
That fallback lists concrete origins in Europe, Australia, and North America
plus two CDN entry points, so failover always reaches a different machine. The
GeoAPI orders it for each client; the first entry is a host whose GeoAPI
answered when the list was chosen.

The four verified destinations with the lowest workload estimates become
`CVMFS_SERVER_URL`. If the
shortlist is entirely CDN endpoints, also test the fastest screened direct
endpoint and retain it as a fallback if its data checks succeed. With
`CVMFS_USE_GEOAPI=no`, the client walks this order. Runtime failover settings
(`CVMFS_LOW_SPEED_LIMIT`, `CVMFS_TIMEOUT`, `CVMFS_MAX_RETRIES`,
`CVMFS_HOST_RESET_AFTER`) remain in
[`config/cvmfs/default.local`](../../config/cvmfs/default.local).

File-bundle prefetching is enabled with `CVMFS_PREFETCH_FILEBUNDLES=yes` in
`config/cvmfs/default.local`. CVMFS 2.14.1 reads this option at mount time.
When a published module opens its bundle trigger, CVMFS prefetches the listed
startup files asynchronously. Loading the module does not wait for those
downloads. The feature remains experimental and requires a publisher-defined
bundle; enabling the client alone does not select files to download. See the
[CVMFS file-bundle documentation](https://cvmfs.readthedocs.io/en/2.14/cpt-file-bundles/).

Rankings are cached in `~/.cache/neurodesktop/cvmfs-selection.env` for one day.
At startup, the primary must complete both hash-verified samples and the
latency requests within twice its recorded workload estimate. The first fallback
must also complete them; a failed transfer or a fallback with an estimate more
than 20% lower triggers a full re-ranking. The primary must also serve a
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
first mounts the authoritative Stratum 0 repository and compares it with
neurocommand's desired container inventory. Only after that source is complete
does it fan out across the advertised replicas and the fallback-list job. This
turns a publication omission into one source failure instead of an identical
failure from every downstream replica.
The replica matrix is exactly the selector's candidate pool, including direct
Stratum 1 servers, geo-DNS names, and Cloudflare aliases. A unit test keeps
the two lists identical, so every mirror that users can select is checked for
a complete inventory. Both its single-server and fallback-list
jobs first run
[`check_cvmfs_mount.sh`](../../.github/workflows/check_cvmfs_mount.sh). It triggers
a repository read with a 45-second timeout and confirms a `fuse` or `fuse.cvmfs`
mount with source `cvmfs2` using `findmnt`. An unavailable mount produces one
mount-failure annotation and stops before inventory download, replica propagation
waits, or the FSL file copy.
The checker permits at most two read-and-mount checks within a shared
180-second verification budget. Each command's timeout is limited by the
remaining budget, reserving five seconds for kill grace and one second for
Bash's whole-second clock rounding. A failed first check records command exit
status and elapsed time before the second attempt. When the first repository
read fails, the checker waits 61 seconds before the second read, because autofs
[caches failed mounts for 60 seconds](https://man7.org/linux/man-pages/man8/automount.8.html)
and an immediate read only replays the cached failure. After a timed-out
read the wait is best effort: killing `ls` does not stop the autofs mount
attempt, which can fail later and start its cache then. It skips the second
read when the budget cannot fit that wait plus a 10-second read. A mount-type
failure after a successful read retries immediately. A recovered check is
visible in the log. Both attempts use the same configured
mirror and repository; the checker does not restart autofs or change servers.
Failure diagnostics include effective client configuration, the repository probe
and status, autofs status and journal, and client logs. Each diagnostic has a
10-second timeout. The diagnostic timeout runs under noninteractive sudo so it
can terminate privileged child processes. All mount checks and diagnostics have
a five-second kill grace period; diagnostic failures preserve the mount-failure
exit status.
The seven diagnostics can take a further 105 seconds including kill grace.
The verification budget does not include that diagnostic allowance.
Once the mount passes, both jobs use
[`check_cvmfs_inventory.sh`](../../.github/workflows/check_cvmfs_inventory.sh),
which fails unavailable or empty inventory downloads and reports every missing
container in the snapshot before failing. When the first comparison finds a
mismatch, the checker synchronously asks the mounted client to remount the
latest catalog and repeats the complete comparison against the same downloaded
inventory. The Stratum 0 job fails if that comparison still finds missing
entries. Replica jobs set `CVMFS_INVENTORY_REPLICA_RETRY=true` to allow one
further refresh after a 240-second propagation grace period. This wait does not
guarantee that replication finishes. Persistent omissions still fail the job.
The default is `false`, and complete inventories return without waiting. Every comparison checks all
entries from the original snapshot, including entries that were present earlier.
Each refresh has a 45-second timeout and a five-second kill grace period, uses
noninteractive sudo when needed, and records catalog status before and after.
A refresh failure or timeout still checks the inventory. After the final
comparison, the checker reports every remaining omission and exits with code 2.

The scheduled [object-storage health workflow](../../.github/workflows/test-objectstorage.yml)
checks a validated snapshot of the same Neurocommand inventory. Each object's
HEAD request must finish successfully with HTTP 200. Redirects, other HTTP
responses except 404, and transport failures stop the check. Missing objects
receive up to three total checks, with a 30-second delay between attempts by
default. Persistent omissions produce one annotation per object and exit code 2.
The checker reports publication omissions; it does not upload or repair objects.

## Module cache warm-up

Lmod lists modules by reading every modulefile in `MODULEPATH`. On a cold
CVMFS cache, the first `ml av` therefore makes about 2,000 sequential CVMFS
requests. In a test container this took 55 seconds. Afterwards, Lmod reads one
per-user spider cache file, and `ml av` takes about 0.1 seconds. Request latency
sets that cost, so it is largest for users far from every mirror.

After the deferred worker's CVMFS attempt, it launches
[`warm_module_cache.sh`](../../config/jupyter/warm_module_cache.sh) in the
background. The script runs as the notebook user with a clean environment and
`nice -n 10`. After initializing the user's environment, it reads the cache
directory from `module --config-json` and invalidates that cache so a restart
lists tools published since the last build. The installed Lmod 8.7.60 uses
`~/.cache/lmod` and does not honor `XDG_CACHE_HOME`; the warm-up follows Lmod's
reported directory, including site overrides. It creates cache directories and
files with a private umask, then runs
`module avail` with `LMOD_SHORT_TIME=0` so Lmod writes the cache even when the
build is quick. The build does not delay the worker's completion marker or
Slurm. It is skipped when CVMFS modules are unavailable or when
`NEURODESKTOP_LMOD_CACHE_WARMUP=0`, and it stops after 15 minutes. Failures are
logged and leave Lmod to build the cache on demand. An `ml av` that runs during
the warm-up still benefits from the modulefiles the warm-up has already
fetched. The warm-up runs whenever the deferred worker does, which includes
eager CVMFS with the default lazy Slurm. It does not run when both CVMFS and
Slurm use eager mode.

## Build-time CVMFS setup

The active repository configuration is generated at startup by
`cvmfs_server_select.sh` (see above). The image bakes in
[`config/cvmfs/neurodesk.ardc.edu.au.conf`](../../config/cvmfs/neurodesk.ardc.edu.au.conf)
as a static default so mounts that happen before the selection ran still work;
PR acceptance and production image tests generate a verified mirror ranking
on the host before mounting. Other CVMFS host jobs can use the static default.

## Neurocommand

Neurocommand is cloned from
[`neurodesk/neurocommand`](https://github.com/neurodesk/neurocommand) during the
build. It provides the CLI and module system for neuroimaging tools, uses Lmod
for module management, and stores containers in
`/neurodesktop-storage/containers`.

Neurocommand may keep bundled module files in a real
`/neurocommand/local/containers` directory. Image build and startup preserve
that directory and create the storage symlink only when the path is absent.
Existing files and symlinks are also preserved. The launchers use
`NEURODESKTOP_LOCAL_CONTAINERS` for the container store; Sherlock sets it to
the shared group store bound into the image.

The notebook user owns `/neurocommand`. The Neurodesk menu's **Update**
entry runs `/usr/local/bin/neurodesk-update`, which invokes
`bash build.sh --update` in that checkout as the current user. The helper
restores the generated `local/bin/update.sh` launcher after each rebuild,
including failed builds. The terminal waits for Enter and preserves the update's
exit status. Menu updates require no sudo access.

Root startup repairs checkout ownership when the notebook UID or GID changes.
It does not execute checkout code or traverse symbolic links, and skips files
with multiple hard links. Read-only or foreign-owned HPC checkouts refuse the
update with a permission error. The helper also refuses root execution.
