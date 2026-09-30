"""Bounded CVMFS mirror benchmarks, invoked by cvmfs_server_select.sh.

curl supplies the same HTTP/proxy transport used by the existing selector.
Catalogs supply immutable sample hashes; no CVMFS mount or hard-coded tool
version is needed. Hash checking detects corruption, not manifest authenticity;
the CVMFS client remains responsible for repository signature verification.
"""

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shlex
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import uuid
import zlib

REPO = "neurodesk.ardc.edu.au"
CACHE_VERSION = 2
MAX_BYTES = 8 * 1024 * 1024
MAX_CATALOG_BYTES = 64 * 1024 * 1024
FINALISTS = 5
BUDGET_SECONDS = 180
HASH = re.compile(r"[0-9a-f]{40}")


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


def object_path(digest, suffix):
    if not HASH.fullmatch(digest) or suffix not in {"C", "P", ""}:
        raise ValueError("invalid repository object")
    return f"data/{digest[:2]}/{digest[2:]}{suffix}"


class Benchmark:
    def __init__(self):
        self.deadline = time.monotonic() + BUDGET_SECONDS

    def fetch(self, base, path, *, digest=None, timeout=10):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return None
        limit = min(timeout, remaining)
        url = f"{base}/cvmfs/{REPO}/{path}?cvmfsselect={uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix="cvmfs-probe-") as directory:
            body = Path(directory) / "body"
            try:
                proc = subprocess.run(
                    ["curl", "--silent", "--show-error", "--fail", "--noproxy", "*",
                     "--connect-timeout", str(min(3, limit)), "--max-time", str(limit),
                     "--max-filesize", str(MAX_BYTES), "--output", str(body),
                     "--write-out", "%{http_code} %{time_total} %{remote_ip}", url],
                    capture_output=True, text=True, timeout=limit + 1,
                )
                fields = proc.stdout.split()
                if proc.returncode or len(fields) != 3 or fields[0] != "200":
                    return None
                elapsed = float(fields[1])
                if not math.isfinite(elapsed) or elapsed <= 0:
                    return None
                data = body.read_bytes()
                if not data or len(data) > MAX_BYTES:
                    return None
                if digest and hashlib.sha1(data).hexdigest() != digest:
                    return None
                return {"data": data, "seconds": elapsed, "bytes": len(data),
                        "speed": len(data) / elapsed, "ip": fields[2]}
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return None

    def probe(self, base):
        replies = []
        for _ in range(2):
            result = self.fetch(base, ".cvmfspublished", timeout=5)
            if not result:
                continue
            manifest = result["data"].split(b"\n--\n", 1)[0].decode("ascii", errors="replace")
            digest = re.search(r"^C([0-9a-f]{40})$", manifest, re.MULTILINE)
            revision = re.search(r"^S(\d+)$", manifest, re.MULTILINE)
            if digest and revision:
                replies.append({"base": base, "hash": digest[1], "revision": int(revision[1]),
                                "latency": result["seconds"], "ip": result["ip"]})
        if not replies:
            log(f"  {base}: manifest unavailable or invalid")
            return None
        return min(replies, key=lambda r: (-r["revision"], r["latency"]))

    def sample(self, base, obj):
        return self.fetch(base, object_path(obj["hash"], obj["suffix"]), digest=obj["hash"])


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
            chunks = db.execute(
                "SELECT DISTINCT lower(hex(hash)), size FROM chunks "
                "WHERE length(hash)=20 AND size BETWEEN 262144 AND 7340032 "
                "ORDER BY size, hex(hash) LIMIT 256"
            ).fetchall()
            nested = db.execute(
                "SELECT sha1 FROM nested_catalogs ORDER BY path LIMIT 2"
            ).fetchall()
    samples = []
    for digest, _ in ([chunks[0], chunks[-1]] if chunks else []):
        obj = {"hash": digest, "suffix": "P"}
        if obj not in samples:
            samples.append(obj)
    return samples, [row[0] for row in nested if isinstance(row[0], str) and HASH.fullmatch(row[0])]


def discover_samples(benchmark, source, root):
    samples = []
    queue = [root]
    seen = {source["hash"]}
    for _ in range(3):
        if not queue:
            break
        result = queue.pop(0)
        try:
            found, nested = catalog_samples(result["data"])
        except (ValueError, zlib.error, sqlite3.Error):
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
        if not math.isfinite(meta["baseline"]) or meta["baseline"] <= 0:
            return None
        return meta
    except (OSError, ValueError, KeyError, TypeError):
        return None


def reuse_cache(benchmark, meta):
    def speed(base):
        results = [benchmark.sample(base, obj) for obj in meta["samples"]]
        if not all(results):
            return 0
        return sum(r["bytes"] for r in results) / sum(r["seconds"] for r in results)

    primary = speed(meta["bases"][0])
    if not primary or primary < meta["baseline"] * 0.5:
        log("Cached primary failed its transfer check or fell below half its measured speed.")
        return False
    # Compare both objects, so a lucky small response cannot trigger re-ranking
    # when the cached primary still wins on the representative data workload.
    if len(meta["bases"]) > 1:
        challenger = speed(meta["bases"][1])
        if challenger > primary * 1.2:
            log("Cached fallback is over 20% faster than the primary; re-ranking.")
            return False
    # A cached historical object alone does not establish repository health.
    return benchmark.probe(meta["bases"][0]) is not None


def select(benchmark, hosts):
    log("Stage 1: probing candidate hosts in parallel...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(20, len(hosts))) as pool:
        reachable = [row for row in pool.map(benchmark.probe, hosts) if row]
    reachable.sort(key=lambda row: (-row["revision"], row["latency"], row["base"]))
    unique = []
    destinations = set()
    for row in reachable:
        key = destination(row)
        if key in destinations:
            log(f"  {row['base']}: duplicate destination {row['ip']}, skipping")
            continue
        destinations.add(key)
        unique.append(row)
    # Get a verified common catalog so different replica revisions cannot win
    # just because they serve a smaller catalog. Prefer the newest revision.
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
    eligible = [r for r in unique if r["revision"] >= source["revision"]]
    log(f"Stage 2: screening all {len(eligible)} distinct current destinations by throughput...")
    # Rotate the order so startup ordering does not repeatedly favor one host.
    random.SystemRandom().shuffle(eligible)
    screened = []
    for row in eligible:
        result = benchmark.sample(row["base"], catalog)
        if result:
            screened.append((result["speed"], row))
            log(f"  {row['base']}: catalog {result['speed'] / 1024:.0f} KiB/s")
        else:
            log(f"  {row['base']}: incomplete, corrupt or unavailable catalog")
    screened.sort(key=lambda item: (-item[0], item[1]["base"]))
    # Keep a direct-origin candidate in the finals even if CDN edges dominate.
    finalists = screened[:FINALISTS]
    if finalists and all(is_cdn(row["base"]) for _, row in finalists):
        direct = next((item for item in screened if not is_cdn(item[1]["base"])), None)
        if direct:
            finalists.append(direct)
    log(f"Stage 3: validating {len(finalists)} finalists with two data-object transfers...")
    measurements = {row["base"]: [] for _, row in finalists}
    for obj in samples:
        order = list(measurements)
        random.SystemRandom().shuffle(order)
        for base in order:
            # One failed transfer disqualifies a finalist rather than rewarding
            # a lucky best-of-two result or accepting an HTTP 200 partial body.
            if measurements[base] is None:
                continue
            result = benchmark.sample(base, obj)
            if result:
                measurements[base].append(result)
            else:
                measurements[base] = None
                log(f"  {base}: data transfer failed; excluded")
    ranked = []
    for base, results in measurements.items():
        if results and len(results) == len(samples):
            score = sum(r["bytes"] for r in results) / sum(r["seconds"] for r in results)
            ranked.append((score, base))
            log(f"  {base}: verified data {score / 1024:.0f} KiB/s")
    # A catalog can be healthy while its data backend is unavailable. Fill
    # missing fallback slots from the rest of the measured pool, and keep
    # looking for a working direct origin if CDN finalists are all that survived.
    for _, row in screened:
        if len(ranked) >= 4 and any(not is_cdn(base) for _, base in ranked):
            break
        base = row["base"]
        if base in measurements or (len(ranked) >= 4 and is_cdn(base)):
            continue
        results = []
        for obj in samples:
            result = benchmark.sample(base, obj)
            if not result:
                log(f"  {base}: replacement data transfer failed; excluded")
                break
            results.append(result)
        if len(results) == len(samples):
            score = sum(r["bytes"] for r in results) / sum(r["seconds"] for r in results)
            ranked.append((score, base))
            log(f"  {base}: replacement verified data {score / 1024:.0f} KiB/s")
    ranked.sort(reverse=True)
    if not ranked:
        return None
    chosen = ranked[:4]
    if all(is_cdn(base) for _, base in chosen):
        direct = next((item for item in ranked if not is_cdn(item[1])), None)
        if direct:
            chosen.append(direct)
    return {"version": CACHE_VERSION, "pool": hosts,
            "bases": [base for _, base in chosen], "samples": samples,
            "baseline": chosen[0][0], "complete": time.monotonic() < benchmark.deadline}


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
    raise SystemExit(main())
