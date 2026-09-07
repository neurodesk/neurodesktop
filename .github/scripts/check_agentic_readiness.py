"""Check subscription storage on the runner and daemon without model requests."""

import fcntl
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess


spec = importlib.util.spec_from_file_location("worker", Path(__file__).with_name("agentic_worker.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def check_auth_directory(auth):
    auth = auth.resolve(strict=True)
    for path in (auth, auth / "auth.json"):
        metadata = path.lstat()
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError("Codex directory and auth.json must be private to the runner user")
        if path != auth and not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Codex auth.json must be a regular file")
    worker.token_values(auth)
    return auth


def main():
    auth = check_auth_directory(Path(os.environ["AGENTIC_CODEX_HOME"]))
    with (auth / ".neurodesktop-worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A hash compares runner and daemon mounts without printing credentials.
        expected = hashlib.sha256((auth / "auth.json").read_bytes()).hexdigest()
        probe = "import hashlib; print(hashlib.sha256(open('/codex/auth.json','rb').read()).hexdigest())"
        result = subprocess.run([
            "docker", "run", "--rm", "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--mount", f"type=bind,src={auth},dst=/codex,readonly",
            worker.IMAGE, "python", "-c", probe,
        ], capture_output=True, text=True, timeout=30)
        if result.returncode or result.stdout.strip() != expected:
            raise ValueError("Runner and Docker daemon cannot read the same authentication file")
    print("Persistent subscription storage passed. Live authentication still requires a model canary.")


if __name__ == "__main__":
    main()
