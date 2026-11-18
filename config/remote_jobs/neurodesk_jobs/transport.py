"""One interactive SSH master, reused by every later command."""

import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import zipfile

from .core import JobError, canonical, host as checked_host

# One interactive login (with MFA) should last a working session; sites such as
# Bunya ask for an Okta push on every new connection.
PERSIST = "4h"

# Runs in the login shell's noninteractive environment, so PATH, HOME and the
# site's scratch variables are what later SSH commands see. Prints one JSON line.
DISCOVER = r"""
python=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
  found=$(command -v "$candidate" 2>/dev/null) || continue
  "$found" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null && { python=$found; break; }
done
scratch=""
for candidate in "${SCRATCH:-}" "/scratch/user/$USER" "/scratch/users/$USER" "/scratch/$USER"; do
  if [ -n "$candidate" ] && [ -d "$candidate" ] && [ -w "$candidate" ]; then scratch=$candidate; break; fi
done
[ -n "$python" ] || { echo "No python3 >= 3.9 on the login PATH: $PATH" >&2; exit 3; }
exec "$python" -c 'import json, os, sys; print(json.dumps({"python": sys.argv[1], "home": os.environ["HOME"], "path": os.environ.get("PATH", ""), "scratch": sys.argv[2]}))' "$python" "$scratch"
"""


class SSH:
    def __init__(self, alias):
        self.host = checked_host(alias)
        self.sockets = Path.home() / ".ssh" / "neurodesk-jobs"
        self.sockets.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.sockets.is_symlink() or self.sockets.stat().st_uid != os.getuid():
            raise JobError("SSH control directory must be private and owned by you")
        os.chmod(self.sockets, 0o700)
        if len(str(self.sockets)) > 55:
            raise JobError("Home path is too long for an SSH control socket")
        self.ssh = shutil.which("ssh")
        if not self.ssh:
            raise JobError("OpenSSH is required")
        self.options = ["-o", f"ControlPath={self.sockets / '%C'}", "-o", "ControlMaster=no",
                        "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                        "-o", "ForwardAgent=no"]

    def connect(self):
        active = subprocess.run([self.ssh, *self.options, "-O", "check", self.host],
                                capture_output=True, timeout=20, check=False)
        if active.returncode == 0:
            return
        # Command-line options precede the defaults: this call alone may prompt,
        # in the user's terminal, including site MFA.
        result = subprocess.run([self.ssh, "-o", "BatchMode=no", "-o", "ControlMaster=yes",
                                 "-o", f"ControlPersist={PERSIST}", "-Nf", *self.options,
                                 self.host], check=False)
        if result.returncode:
            raise JobError(f"Could not log in to {self.host}. Check that `ssh {self.host}` "
                           "works in this terminal, then run connect again")

    def _check(self, result, argv):
        if result.returncode == 255:
            raise JobError(f"No open SSH connection to {self.host} (it may have expired). "
                           f"Run: neurodesk jobs connect {self.host}")
        if result.returncode:
            stderr = result.stderr.decode(errors="replace").strip() if result.stderr else ""
            # The helper's own errors are already written for the user.
            if stderr.startswith("neurodesk jobs: "):
                raise JobError(stderr[len("neurodesk jobs: "):][-3000:])
            raise JobError(f"{argv[0]} on {self.host} failed ({result.returncode}): {stderr[-3000:]}")

    def command(self, argv, source=None, timeout=300):
        argv = [str(arg) for arg in argv]
        try:
            result = subprocess.run([self.ssh, *self.options, self.host, shlex.join(argv)],
                                    input=source, capture_output=True, timeout=timeout,
                                    check=False)
        except subprocess.TimeoutExpired as exc:
            raise JobError(f"{argv[0]} on {self.host} timed out") from exc
        self._check(result, argv)
        return result.stdout.decode().strip()

    def discover(self):
        return json.loads(self.command(["sh", "-c", DISCOVER], timeout=60))

    def install(self, site):
        """Copy this exact helper version to the cluster; return its path."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(zipfile.ZipInfo("__main__.py"), "from neurodesk_jobs.cli import main\nmain()\n")
            for file in sorted(Path(__file__).parent.glob("*.py")):
                # Fixed timestamps give each helper version a stable identity.
                archive.writestr(zipfile.ZipInfo("neurodesk_jobs/" + file.name), file.read_bytes())
        content = buffer.getvalue()
        sha = hashlib.sha256(content).hexdigest()
        target = str(Path(site["state_root"]) / "helpers" / f"{sha}.pyz")
        script = (
            "import hashlib,os,pathlib,sys,tempfile; "
            "p=pathlib.Path(sys.argv[1]); b=sys.stdin.buffer.read(); "
            "assert hashlib.sha256(b).hexdigest()==sys.argv[2]; "
            "p.parent.mkdir(parents=True,exist_ok=True,mode=0o700); "
            "fd,t=tempfile.mkstemp(dir=p.parent); "
            "f=os.fdopen(fd,'wb'); f.write(b); f.flush(); os.fsync(f.fileno()); f.close(); "
            "os.replace(t,p)"
        )
        self.command([site["python"], "-c", script, target, sha], source=content)
        return target

    def rpc(self, site, helper, operation, value, timeout=300):
        return json.loads(self.command([site["python"], helper, "_rpc", operation],
                                       source=canonical(value), timeout=timeout))

    def upload(self, site, helper, path, sha, size):
        """Stream one file into the cluster's checksummed object store."""
        argv = [site["python"], helper, "_put", site["data_root"], sha, str(size)]
        with open(path, "rb") as stream:
            result = subprocess.run([self.ssh, *self.options, self.host, shlex.join(argv)],
                                    stdin=stream, capture_output=True, timeout=86400,
                                    check=False)
        self._check(result, argv)

    def download(self, site, helper, value, destination):
        """Stream one published result file to a local path."""
        argv = [site["python"], helper, "_get"]
        with open(destination, "xb") as stream:
            result = subprocess.run([self.ssh, *self.options, self.host, shlex.join(argv)],
                                    input=canonical(value), stdout=stream,
                                    stderr=subprocess.PIPE, timeout=86400, check=False)
        self._check(result, argv)
