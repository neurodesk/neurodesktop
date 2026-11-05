"""One interactive SSH master reused by RPC, Git, DataLad and ORA."""

import hashlib
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import zipfile

from .core import JobError, canonical, run


class SSH:
    def __init__(self, site, state):
        self.site = site
        self.root = Path(state) / "transport"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.sockets = Path.home() / ".ssh" / "neurodesk-jobs"
        self.sockets.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.sockets.is_symlink() or self.sockets.stat().st_uid != os.getuid():
            raise JobError("SSH control directory must be private and owned by you")
        os.chmod(self.sockets, 0o700)
        self.control = str(self.sockets / "%C")
        if len(str(self.sockets)) > 55:
            raise JobError("Home path is too long for an SSH control socket")
        self.ssh = shutil.which("ssh")
        if not self.ssh:
            raise JobError("OpenSSH is required")
        self.options = ["-o", f"ControlPath={self.control}", "-o", "ControlMaster=no",
                        "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                        "-o", "ForwardAgent=no"]
        for tool in ("ssh", "scp"):
            binary = shutil.which(tool)
            if not binary:
                raise JobError(f"OpenSSH {tool} is required")
            wrapper = self.root / tool
            wrapper.write_text("#!/bin/sh\nexec " + shlex.join([binary, *self.options]) + ' "$@"\n')
            wrapper.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                        GIT_SSH_COMMAND=shlex.quote(str(self.root / "ssh")),
                        DATALAD_SSH_MULTIPLEX__CONNECTIONS="false")
        count = int(self.env.get("GIT_CONFIG_COUNT", "0"))
        self.env.update({"GIT_CONFIG_COUNT": str(count + 1),
                         f"GIT_CONFIG_KEY_{count}": "annex.sshcaching",
                         f"GIT_CONFIG_VALUE_{count}": "false"})

    def connect(self):
        for host in sorted({self.site["host"], self.site["transfer_host"]}):
            active = subprocess.run([self.ssh, *self.options, "-O", "check", host],
                                    capture_output=True, timeout=20, check=False)
            if active.returncode == 0:
                continue
            # Command-line options precede the wrapper defaults: this call alone
            # may prompt, in the user's terminal, including site MFA.
            result = subprocess.run([self.ssh, "-o", "BatchMode=no", "-o", "ControlMaster=yes",
                                     "-o", "ControlPersist=600", "-Nf", *self.options, host],
                                    check=False)
            if result.returncode:
                raise JobError(f"Interactive authentication failed for {host}")

    def command(self, host, argv, source=None, timeout=300):
        return run([self.ssh, *self.options, host, shlex.join(argv)],
                   input=source, timeout=timeout)

    def remote(self, argv, host=None, source=None, timeout=300):
        return self.command(host or self.site["host"],
                            ["env", f"PATH={self.site['path']}", *argv], source, timeout)

    def install(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(zipfile.ZipInfo("__main__.py"), "from neurodesk_jobs.cli import main\nmain()\n")
            for file in sorted(Path(__file__).parent.glob("*.py")):
                # Fixed timestamps give each helper version a stable identity.
                info = zipfile.ZipInfo("neurodesk_jobs/" + file.name)
                archive.writestr(info, file.read_bytes())
        content = buffer.getvalue()
        sha = hashlib.sha256(content).hexdigest()
        target = str(Path(self.site["state_root"]) / "helpers" / f"{sha}.pyz")
        script = (
            "import hashlib,os,pathlib,sys,tempfile; "
            "p=pathlib.Path(sys.argv[1]); b=sys.stdin.buffer.read(); "
            "assert hashlib.sha256(b).hexdigest()==sys.argv[2]; "
            "p.parent.mkdir(parents=True,exist_ok=True,mode=0o700); "
            "fd,t=tempfile.mkstemp(dir=p.parent); "
            "f=os.fdopen(fd,'wb'); f.write(b); f.flush(); os.fsync(f.fileno()); f.close(); "
            "os.replace(t,p)"
        )
        self.remote([self.site["python"], "-c", script, target, sha], source=content)
        return target

    def rpc(self, helper, operation, value, timeout=300):
        import json

        return json.loads(self.remote([self.site["python"], helper, "_rpc", operation],
                                     source=canonical(value), timeout=timeout))
