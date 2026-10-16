"""Root-owned fixtures for the disposable default-permission Slurm check."""

import json
import os
from pathlib import Path
import pwd
import subprocess
import time
import uuid

import pytest

from test_slurm import _skip_if_slurm_not_expected


def command(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=90).stdout.strip()


@pytest.mark.skipif(os.geteuid() != 0, reason="requires disposable-container root fixtures")
def test_login_environments_are_per_user_and_survive_reconfigure():
    """The real login shell supplies each user's defaults without a shared cache."""
    _skip_if_slurm_not_expected()
    options = [
        (["--export=ALL"], "submitted", "submitted"),
        (["--export=NIL"], None, None),
        (["--export=NONE"], "login", None),
        (["--export=ND_SUBMIT=selected"], "login", "selected"),
        (["--export=ALL", "--get-user-env"], "submitted", "submitted"),
    ]
    for index in range(2):
        user = f"ndenv{uuid.uuid4().hex[:12]}"
        command("useradd", "--create-home", "--user-group", "--shell", "/bin/bash", user)
        try:
            identity = pwd.getpwnam(user)
            home = Path(identity.pw_dir)
            profile = home / ".bash_profile"
            profile.write_text(f"export ND_LOGIN_OWNER={user}\n")
            os.chown(profile, identity.pw_uid, identity.pw_gid)
            command("sacctmgr", "--immediate", "add", "user", user, "Account=default")
            if index:
                old_pid = Path("/run/slurm/slurmd.pid").read_text().strip()
                command("scontrol", "reconfigure")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    pid = Path("/run/slurm/slurmd.pid").read_text().strip()
                    if pid != old_pid and Path(f"/proc/{pid}/exe").exists():
                        break
                    time.sleep(0.1)
                assert pid != old_pid, "Slurmd did not re-exec on reconfigure"
                assert os.readlink(f"/proc/{pid}/exe") == "/opt/neurodesktop/slurm-env/slurmd"
                assert "/opt/neurodesktop/slurm-env/user-env.so" in Path(f"/proc/{pid}/maps").read_text()
            script = home / "check-env.sh"
            script.write_text(
                "#!/bin/bash\nexec /usr/bin/python3 - <<'PY'\n"
                "import json, os\nfrom pathlib import Path\n"
                "print(json.dumps({'uid': os.getuid(), 'owner': os.getenv('ND_LOGIN_OWNER'), "
                "'submit': os.getenv('ND_SUBMIT'), 'home': os.getenv('HOME'), "
                "'preload': os.getenv('LD_PRELOAD'), "
                "'shim': 'slurm-env/user-env.so' in Path('/proc/self/maps').read_text()}))\nPY\n"
            )
            for number, (flags, owner, submitted) in enumerate(options):
                output = home / f"result-{number}.json"
                job_id = command(
                    "runuser", "-u", user, "--", "env", "-i", "PATH=/usr/bin:/bin",
                    f"HOME={home}", f"USER={user}", "ND_LOGIN_OWNER=submitted", "ND_SUBMIT=submitted",
                    "sbatch", "--parsable", "--wait", "--time=00:01:00", "--mem=64M",
                    "--chdir", str(home), "--output", str(output), *flags, str(script),
                ).split(";")[0]
                assert job_id.isdigit(), job_id
                actual = json.loads(output.read_text())
                assert actual["uid"] == identity.pw_uid
                assert actual["owner"] == (user if owner == "login" else owner), actual
                assert actual["submit"] == submitted, actual
                if owner == "login":
                    assert actual["home"] == str(home), actual
                assert actual["preload"] is None, actual
                assert actual["shim"] is False, actual
            if index:
                profile.write_text("exit 1\n")
                worker_pid = Path("/run/slurm/slurmd.pid").read_text().strip()
                failed_job = command(
                    "runuser", "-u", user, "--", "sbatch", "--parsable",
                    "--export=NONE", "--time=00:01:00", "--mem=64M",
                    "--chdir", str(home), "--output", str(home / "failed.out"), str(script),
                ).split(";")[0]
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    status = command("squeue", "--noheader", "--jobs", failed_job, "--format=%T|%r")
                    if status == "PENDING|user env retrieval failed requeued held":
                        break
                    time.sleep(0.5)
                assert status == "PENDING|user env retrieval failed requeued held", status
                assert Path("/run/slurm/slurmd.pid").read_text().strip() == worker_pid
                assert "\nState:\tZ" not in Path(f"/proc/{worker_pid}/status").read_text()
        finally:
            command("scancel", "--user", user)
            command("sacctmgr", "--immediate", "delete", "user", "where", f"name={user}")
            command("userdel", "--remove", user)
