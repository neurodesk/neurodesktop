"""Bounded CVMFS mirror benchmarks, invoked by cvmfs_server_select.sh.

curl supplies the same HTTP/proxy transport used by the existing selector.
Catalogs supply immutable sample hashes; no CVMFS mount or hard-coded tool
version is needed. Hash checking detects corruption, not manifest authenticity;
the CVMFS client remains responsible for repository signature verification.
"""

import argparse
from collections import Counter
import concurrent.futures
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import random
import resource
import re
import shlex
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid
import zlib

REPO = "neurodesk.ardc.edu.au"
CACHE_VERSION = 3
MAX_BYTES = 8 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
FINALISTS = 5
BUDGET_SECONDS = 180
# Mirrors are ranked by the estimated time of a reference workload: listing
# modules and starting a tool issue roughly a thousand sequential small-object
# requests and read a few hundred MiB of file chunks. Request latency and bulk
# throughput therefore both count; the mix is not tuned to any one region.
WORKLOAD_REQUESTS = 1000
WORKLOAD_BYTES = 256 * 1024 * 1024
LATENCY_PROBES = 3
# A replica whose last successful snapshot is at least this old is a WARNING in
# the upstream cvmfs-servermon "updated" test. Use the same age before ranking a
# replica that has fallen behind its corroborated peers below current ones.
STALE_AFTER_SECONDS = 8 * 3600
HASH = re.compile(r"[0-9a-f]{40}")
SNAPSHOT_DATE = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"{1,2}(\d{1,2}) (\d\d):(\d\d):(\d\d) UTC (\d{4})\n?")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def log(message):
    print(f"[cvmfs-select] {message}", flush=True)


def valid_base(base):
    try:
        url = urlsplit(base)
        return (url.scheme in {"http", "https"} and url.hostname and
                url.port != 0 and not url.username and not url.password and
                not url.query and not url.fragment and url.path in {"", "/"} and
                not re.search(r'[\s\x00-\x1f"\'`$;\\]', base))
    except ValueError:
        return False


def is_cdn(base):
    return urlsplit(base).hostname.endswith(".openhtc.io")


def destination(candidate):
    """Do not collapse CDN virtual hosts that share an anycast edge address."""
    url = urlsplit(candidate["base"])
    identity = url.hostname if is_cdn(candidate["base"]) else candidate["ip"]
    return url.scheme, identity, url.port or (443 if url.scheme == "https" else 80)


def is_canonical(base, timeout=3):
    """Whether the hostname is its own DNS target rather than a CNAME alias.

    A geo-DNS alias can resolve elsewhere when the client mounts later, and
    could then duplicate another configured entry. The resolver has no timeout
    of its own, so a lookup that fails or outlasts *timeout* counts as canonical.
    """
    host = urlsplit(base).hostname
    answer = []

    def lookup():
        try:
            answer.append(socket.gethostbyname_ex(host)[0].rstrip(".").lower() == host)
        except (OSError, UnicodeError):
            pass

    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    worker.join(max(0, timeout))
    return answer[0] if answer else True


def distinct_destinations(rows):
    """Keep one row per destination, preferring a concrete name over an alias."""
    unique = []
    destinations = set()
    for row in sorted(rows, key=lambda r: (not r["canonical"], r["latency"], r["base"])):
        key = destination(row)
        if key in destinations:
            log(f"  {row['base']}: duplicate destination {row['ip']}, skipping")
            continue
        destinations.add(key)
        unique.append(row)
    return unique


def estimate(measurement):
    """Seconds for the reference workload at the measured latency and throughput."""
    seconds = WORKLOAD_REQUESTS * statistics.median(measurement["latencies"])
    if measurement["bytes"]:
        seconds += WORKLOAD_BYTES * measurement["transfer"] / measurement["bytes"]
    return seconds


def describe(measurement):
    rate = measurement["bytes"] / max(measurement["transfer"], 1e-6) / 1024
    return (f"{statistics.median(measurement['latencies']) * 1000:.0f} ms/request, "
            f"{rate:.0f} KiB/s, workload estimate {estimate(measurement):.0f} s")


def parse_manifest(data):
    """Return the root catalog hash, revision and optional publication time."""
    manifest = data.split(b"\n--\n", 1)[0].decode("ascii", errors="replace")
    digest = re.search(r"^C([0-9a-f]{40})$", manifest, re.MULTILINE)
    revision = re.search(r"^S(\d{1,20})$", manifest, re.MULTILINE)
    if not digest or not revision:
        return None
    published = re.search(r"^T(\d{1,12})$", manifest, re.MULTILINE)
    return {"hash": digest[1], "revision": int(revision[1]),
            "published": int(published[1]) if published else None}


def parse_snapshot_date(data):
    """Parse the `date --utc` text a Stratum 1 writes after each snapshot."""
    match = SNAPSHOT_DATE.fullmatch(data.decode("ascii", errors="replace"))
    if not match:
        return None
    month, *fields = match.groups()
    day, hour, minute, second, year = map(int, fields)
    try:
        return datetime.datetime(year, MONTHS.index(month) + 1, day, hour, minute, second,
                                 tzinfo=datetime.UTC).timestamp()
    except ValueError:
        return None


def object_path(digest, suffix):
    if not HASH.fullmatch(digest) or suffix not in {"C", "P", ""}:
        raise ValueError("invalid repository object")
    return f"data/{digest[:2]}/{digest[2:]}{suffix}"


class Benchmark:
    def __init__(self):
        self.deadline = time.monotonic() + BUDGET_SECONDS

    def fetch(self, base, path, *, digest=None, timeout=10, bust=True):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return None
        limit = min(timeout, remaining)
        url = f"{base}/cvmfs/{REPO}/{path}"
        if bust:
            url += f"?cvmfsselect={uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix="cvmfs-probe-") as directory:
            body = Path(directory) / "body"
            try:
                proc = subprocess.run(
                    ["curl", "--silent", "--show-error", "--fail", "--noproxy", "*",
                     "--connect-timeout", str(min(3, limit)), "--max-time", str(limit),
                     "--max-filesize", str(MAX_BYTES), "--output", str(body),
                     "--write-out",
                     "%{http_code} %{time_total} %{remote_ip} %{time_pretransfer} "
                     "%{time_starttransfer}", url],
                    capture_output=True, text=True, timeout=limit + 1,
                )
                fields = proc.stdout.split()
                if proc.returncode or len(fields) != 5 or fields[0] != "200":
                    return None
                elapsed, ready, first_byte = (float(fields[i]) for i in (1, 3, 4))
                if (not all(math.isfinite(t) for t in (elapsed, ready, first_byte))
                        or elapsed <= 0 or not 0 <= ready <= first_byte <= elapsed):
                    return None
                data = body.read_bytes()
                if not data or len(data) > MAX_BYTES:
                    return None
                if digest and hashlib.sha1(data).hexdigest() != digest:
                    return None
                # The CVMFS client reuses connections, so request latency and
                # transfer time both exclude DNS, TCP and TLS setup.
                return {"data": data, "seconds": elapsed, "bytes": len(data),
                        "speed": len(data) / elapsed, "ip": fields[2],
                        "latency": first_byte - ready, "transfer": elapsed - first_byte}
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return None

    def probe(self, base):
        replies = []
        for _ in range(2):
            result = self.fetch(base, ".cvmfspublished", timeout=5)
            manifest = parse_manifest(result["data"]) if result else None
            if manifest:
                replies.append({"base": base, "hash": manifest["hash"],
                                "revision": manifest["revision"],
                                "latency": result["seconds"], "request": result["latency"],
                                "ip": result["ip"]})
        if not replies:
            log(f"  {base}: manifest unavailable or invalid")
            return None
        timeout = min(3, self.deadline - time.monotonic())
        return {**min(replies, key=lambda r: r["latency"]),
                "canonical": is_canonical(base, timeout)}

    def view(self, base):
        """Read the manifest at the URL the CVMFS client requests.

        Performance probes add a unique query string, which bypasses CDN edge
        caches. The plain URL shows the revision a client would actually mount.
        """
        result = self.fetch(base, ".cvmfspublished", timeout=3, bust=False)
        manifest = parse_manifest(result["data"]) if result else None
        return {**manifest, "base": base, "ip": result["ip"]} if manifest else None

    def last_snapshot(self, base):
        """The origin's last successful snapshot time, bypassing edge caches."""
        result = self.fetch(base, ".cvmfs_last_snapshot", timeout=3)
        return parse_snapshot_date(result["data"]) if result and result["bytes"] <= 64 else None

    def sample(self, base, obj):
        return self.fetch(base, object_path(obj["hash"], obj["suffix"]), digest=obj["hash"])

    def measure(self, base, objects):
        """Transfer every data object, then repeat small requests for latency.

        Any failed transfer disqualifies the mirror rather than rewarding a
        lucky partial result.
        """
        results = []
        for path, digest in ([(object_path(o["hash"], o["suffix"]), o["hash"]) for o in objects]
                             + [(".cvmfspublished", None)] * LATENCY_PROBES):
            result = self.fetch(base, path, digest=digest, timeout=10 if digest else 5)
            if not result:
                return None
            results.append(result)
        bulk = results[:len(objects)]
        return {"latencies": [r["latency"] for r in results],
                "bytes": sum(r["bytes"] for r in bulk),
                "transfer": sum(r["transfer"] for r in bulk)}


def reference_view(views):
    """Newest revision that two independent direct origins serve identically.

    CDN hostnames forward to origins the selector cannot identify, so a CDN
    alias and its own origin would otherwise corroborate each other. Direct
    origins are already one row per destination. One endpoint advertising a
    high revision, or disagreement about the newest revision, sets no reference.
    """
    support = Counter((v["revision"], v["hash"]) for v in views if not is_cdn(v["base"]))
    corroborated = [key for key, count in support.items() if count >= 2]
    if not corroborated:
        return None
    revision, digest = max(corroborated)
    if any(other == revision and claim != digest for other, claim in support):
        return None
    published = [v["published"] for v in views
                 if (v["revision"], v["hash"]) == (revision, digest) and v["published"] is not None]
    # Supporters of one signed manifest share its timestamp; if they do not,
    # use the newest claim so the publication looks no older than it might be.
    return {"revision": revision, "published": max(published) if published else None}


def stale_bases(benchmark, views):
    """Endpoints that serve an old revision long after a corroborated newer one.

    Falling behind is normal while a new revision replicates, so it alone never
    demotes a mirror. The reference must have been published at least
    STALE_AFTER_SECONDS ago, or the mirror's own last successful snapshot must
    be that old. Missing, malformed or future times prove nothing. Demotion
    only changes order: a stale mirror remains a verified fallback.
    """
    reference = reference_view(views)
    if not reference:
        return set()
    now = time.time()
    published = reference["published"]
    old_reference = published is not None and now - published >= STALE_AFTER_SECONDS
    behind = [v["base"] for v in views if v["revision"] < reference["revision"]]
    if old_reference or not behind:
        stale = set(behind)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(behind))) as pool:
            snapshots = dict(zip(behind, pool.map(benchmark.last_snapshot, behind), strict=True))
        stale = {base for base, snapshot in snapshots.items()
                 if snapshot is not None and now - snapshot >= STALE_AFTER_SECONDS}
    for base in sorted(stale):
        log(f"  {base}: serves an older revision than corroborated peers "
            f"(revision {reference['revision']}); ranking it after current mirrors")
    return stale


def catalog_samples(data):
    """Read at most 64 MiB of SQLite catalog and select two different chunks.

    Prefer a small and a large chunk to expose both request latency and bulk
    throughput. A bounded nested-catalog search handles sparse root catalogs.
    """
    unpacker = zlib.decompressobj()
    raw = unpacker.decompress(data, MAX_CATALOG_BYTES + 1)
    if len(raw) > MAX_CATALOG_BYTES or not unpacker.eof or unpacker.unused_data:
        raise ValueError("invalid or oversized catalog")
    with tempfile.TemporaryDirectory(prefix="cvmfs-catalog-") as directory:
        path = Path(directory) / "catalog.db"
        path.write_bytes(raw)
        with sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            # Limit VM work as well as file size when inspecting remote metadata.
            deadline = time.monotonic() + 1
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            chunks = []
            for order in ("ASC", "DESC"):
                chunk = db.execute(
                    "SELECT lower(hex(hash)), size FROM chunks "
                    "WHERE length(hash)=20 AND size BETWEEN 262144 AND 7340032 "
                    f"ORDER BY size {order}, hex(hash) {order} LIMIT 1"
                ).fetchone()
                if chunk:
                    chunks.append(chunk)
            nested = db.execute(
                "SELECT sha1 FROM nested_catalogs ORDER BY path LIMIT 2"
            ).fetchall()
    samples = []
    for digest, _ in ([chunks[0], chunks[-1]] if chunks else []):
        obj = {"hash": digest, "suffix": "P"}
        if obj not in samples:
            samples.append(obj)
    return samples, [row[0] for row in nested if isinstance(row[0], str) and HASH.fullmatch(row[0])]


def inspect_catalog(data, timeout=4):
    """Parse remote SQLite outside the startup process, dropping root authority."""
    identity = {}
    if os.geteuid() == 0:
        nobody = pwd.getpwnam("nobody")
        identity = {"user": nobody.pw_uid, "group": nobody.pw_gid, "extra_groups": []}
    proc = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--inspect-catalog"],
        input=data, capture_output=True, timeout=timeout, cwd="/",
        env={"PATH": os.defpath, "LANG": "C.UTF-8"}, **identity,
    )
    if proc.returncode:
        raise ValueError("catalog worker failed")
    result = json.loads(proc.stdout)
    if os.geteuid() == 0 and result["uid"] == 0:
        raise ValueError("catalog worker retained root")
    return result["samples"], result["nested"]


def catalog_worker():
    """Bound the isolated reader's CPU, address space, file output and input."""
    if os.geteuid() == 0:
        return 1
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_CATALOG_BYTES,) * 2)
    data = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return 1
    try:
        samples, nested = catalog_samples(data)
    except (ValueError, zlib.error, sqlite3.Error):
        return 1
    print(json.dumps({"samples": samples, "nested": nested, "uid": os.geteuid()}))
    return 0


def discover_samples(benchmark, source, root):
    samples = []
    queue = [root]
    seen = {source["hash"]}
    for _ in range(3):
        remaining = benchmark.deadline - time.monotonic()
        if not queue or remaining <= 0:
            break
        result = queue.pop(0)
        try:
            found, nested = inspect_catalog(result["data"], timeout=min(4, remaining))
        except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
            continue
        for obj in found:
            if obj not in samples:
                samples.append(obj)
        if len(samples) >= 2:
            return samples[:2]
        for digest in nested:
            if digest not in seen and len(seen) < 3:
                seen.add(digest)
                child = benchmark.sample(source["base"], {"hash": digest, "suffix": "C"})
                if child:
                    queue.append(child)
    log("Fewer than two data chunks available; supplementing with the verified root catalog.")
    return (samples + [{"hash": source["hash"], "suffix": "C"}] * 2)[:2]


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_config(path, urls, geo=False):
    atomic_write(path, '# Auto-generated by cvmfs_server_select.sh\n'
                 f'CVMFS_USE_GEOAPI={"yes" if geo else "no"}\n'
                 f'CVMFS_SERVER_URL="{urls}"\n'
                 'CVMFS_KEYS_DIR="/etc/cvmfs/keys/ardc.edu.au/"\n')


def server_urls(bases):
    return ";".join(f"{base}/cvmfs/@fqrn@" for base in bases)


def read_cache(path, hosts, ttl):
    """Read data, never source a notebook-user-writable shell file as root."""
    try:
        if path.stat().st_size > 65536:
            return None
        fields = {}
        for line in path.read_text().splitlines():
            if line.startswith("CACHED_"):
                key, value = line.split("=", 1)
                words = shlex.split(value)
                if len(words) != 1:
                    return None
                fields[key] = words[0]
        meta = json.loads(fields["CACHED_METADATA"])
        age = time.time() - int(fields["CACHED_TIMESTAMP"])
        if (meta["version"] != CACHE_VERSION or meta["pool"] != hosts or not 0 <= age < ttl
                or not isinstance(meta["bases"], list) or not meta["bases"]
                or any(base not in hosts for base in meta["bases"])):
            return None
        if fields["CACHED_CVMFS_SERVER_URL"] != server_urls(meta["bases"]):
            return None
        if not isinstance(meta["samples"], list) or len(meta["samples"]) != 2:
            return None
        for obj in meta["samples"]:
            object_path(obj["hash"], obj["suffix"])
        if (not isinstance(meta["baseline"], (int, float)) or isinstance(meta["baseline"], bool)
                or not math.isfinite(meta["baseline"]) or meta["baseline"] <= 0):
            return None
        return meta
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def reuse_cache(benchmark, meta):
    def seconds(base):
        measurement = benchmark.measure(base, meta["samples"])
        return estimate(measurement) if measurement else None

    primary = seconds(meta["bases"][0])
    if primary is None or primary > meta["baseline"] * 2:
        log("Cached primary failed its transfer check or fell below half its measured speed.")
        return False
    # Compare whole workload estimates, so a lucky small response cannot
    # trigger re-ranking when the cached primary still wins overall.
    if len(meta["bases"]) > 1:
        challenger = seconds(meta["bases"][1])
        if challenger is None:
            log("Cached fallback failed its transfer check; re-ranking.")
            return False
        if challenger * 1.2 < primary:
            log("Cached fallback is over 20% faster than the primary; re-ranking.")
            return False
    # A cached historical object alone does not establish repository health.
    if benchmark.probe(meta["bases"][0]) is None:
        return False
    # Only the cached destinations are read, so a stale primary is detected
    # only when two of them are direct origins that corroborate a newer revision.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(meta["bases"])) as pool:
        views = [view for view in pool.map(benchmark.view, meta["bases"]) if view]
    if meta["bases"][0] in stale_bases(benchmark, distinct_views(views)):
        log("Cached primary serves an outdated repository revision; re-ranking.")
        return False
    return True


def distinct_views(views):
    """One manifest view per destination, using the same identity as ranking."""
    unique, seen = [], set()
    for view in views:
        key = destination(view)
        if key not in seen:
            seen.add(key)
            unique.append(view)
    return unique


def select(benchmark, hosts):
    log("Stage 1: probing candidate hosts in parallel...")

    def probe(base):
        row = benchmark.probe(base)
        return {**row, "view": benchmark.view(base)} if row else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(20, len(hosts))) as pool:
        reachable = [row for row in pool.map(probe, hosts) if row]
    unique = distinct_destinations(reachable)
    stale = stale_bases(benchmark, [row["view"] for row in unique if row["view"]])
    # Prefer a corroborated snapshot, never an unauthenticated revision claim.
    # A mirror that cannot serve this snapshot still gets tested against its own
    # catalog; one lying or lagging endpoint must not exclude healthy replicas.
    support = Counter(row["hash"] for row in unique)
    unique.sort(key=lambda row: (-support[row["hash"]], row["latency"], row["base"]))
    source = root = None
    for row in unique:
        result = benchmark.sample(row["base"], {"hash": row["hash"], "suffix": "C"})
        if result:
            source, root = row, result
            break
    if not root:
        return None
    samples = discover_samples(benchmark, source, root)
    catalog = {"hash": source["hash"], "suffix": "C"}
    eligible = list(unique)
    log(f"Stage 2: screening all {len(eligible)} distinct destinations by workload estimate...")
    # Rotate the order so startup ordering does not repeatedly favor one host.
    random.SystemRandom().shuffle(eligible)
    screened = []
    plans = {}
    for row in eligible:
        result = benchmark.sample(row["base"], catalog)
        plan = samples
        if not result and row["hash"] != catalog["hash"]:
            result = benchmark.sample(row["base"], {"hash": row["hash"], "suffix": "C"})
            if result:
                log(f"  {row['base']}: common snapshot unavailable; testing its own verified objects")
                plan = discover_samples(benchmark, row, result)
        if result:
            plans[row["base"]] = plan
            measurement = {"latencies": [row["request"], result["latency"]],
                           "bytes": result["bytes"], "transfer": result["transfer"]}
            screened.append((estimate(measurement), row))
            log(f"  {row['base']}: catalog {describe(measurement)}")
        else:
            log(f"  {row['base']}: incomplete, corrupt or unavailable catalog")
    screened.sort(key=lambda item: (item[1]["base"] in stale, item[0], item[1]["base"]))
    # Keep a direct-origin candidate in the finals even if CDN edges dominate.
    finalists = screened[:FINALISTS]
    if finalists and all(is_cdn(row["base"]) for _, row in finalists):
        direct = next((item for item in screened if not is_cdn(item[1]["base"])), None)
        if direct:
            finalists.append(direct)
    log(f"Stage 3: validating {len(finalists)} finalists with data transfers and "
        f"{LATENCY_PROBES} latency samples each...")
    order = [row["base"] for _, row in finalists]
    random.SystemRandom().shuffle(order)
    measured = {}
    for base in order:
        measured[base] = benchmark.measure(base, plans[base])
        if measured[base]:
            log(f"  {base}: verified {describe(measured[base])}")
        else:
            log(f"  {base}: data transfer failed; excluded")
    ranked = [(base in stale, estimate(m), base) for base, m in measured.items() if m]
    # A catalog can be healthy while its data backend is unavailable. Fill
    # missing fallback slots from the rest of the measured pool, and keep
    # looking for a working direct origin if CDN finalists are all that survived.
    for _, row in screened:
        if len(ranked) >= 4 and any(not is_cdn(base) for *_, base in ranked):
            break
        base = row["base"]
        if base in measured or (len(ranked) >= 4 and is_cdn(base)):
            continue
        measurement = benchmark.measure(base, plans[base])
        if measurement:
            ranked.append((base in stale, estimate(measurement), base))
            log(f"  {base}: replacement verified {describe(measurement)}")
        else:
            log(f"  {base}: replacement data transfer failed; excluded")
    ranked.sort()
    if not ranked:
        return None
    chosen = ranked[:4]
    if all(is_cdn(base) for *_, base in chosen):
        direct = next((item for item in ranked if not is_cdn(item[2])), None)
        if direct:
            chosen.append(direct)
    return {"version": CACHE_VERSION, "pool": hosts,
            "bases": [base for *_, base in chosen], "samples": plans[chosen[0][2]],
            "baseline": chosen[0][1], "complete": time.monotonic() < benchmark.deadline}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-pool", required=True)
    parser.add_argument("--target-config", type=Path, required=True)
    parser.add_argument("--cache-file", type=Path, required=True)
    parser.add_argument("--ttl", type=int, required=True)
    parser.add_argument("--fallback", required=True)
    parser.add_argument("--force-probe", action="store_true")
    args = parser.parse_args()
    hosts = list(dict.fromkeys(base.rstrip("/") for base in args.host_pool.split() if valid_base(base)))
    benchmark = Benchmark()
    cache = read_cache(args.cache_file, hosts, args.ttl) if not args.force_probe else None
    if cache and reuse_cache(benchmark, cache):
        log("Using cached server selection after transfer and challenger checks.")
        write_config(args.target_config, server_urls(cache["bases"]))
        return 0
    selected = select(benchmark, hosts) if hosts else None
    if selected:
        urls = server_urls(selected["bases"])
        write_config(args.target_config, urls)
        log(f"Ranked server list: {urls}")
        if selected["complete"]:
            atomic_write(args.cache_file,
                         f'CACHED_CVMFS_SERVER_URL="{urls}"\n'
                         f'CACHED_TIMESTAMP={int(time.time())}\n'
                         f'CACHED_METADATA={shlex.quote(json.dumps(selected))}\n')
            log(f"Saved CVMFS server selection to cache: {args.cache_file}")
        else:
            args.cache_file.unlink(missing_ok=True)
            log("Benchmark deadline reached; keeping verified results without caching the partial ranking.")
        return 0
    args.cache_file.unlink(missing_ok=True)
    write_config(args.target_config, args.fallback, geo=True)
    log("WARNING: no mirror completed verification; wrote static fallback without caching it.")
    return 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--inspect-catalog"]:
        raise SystemExit(catalog_worker())
    raise SystemExit(main())
