#!/usr/bin/python3
"""Root Glass session adapter. Never expose this program through sudo."""
import os
from pathlib import Path
import pwd
import subprocess
import time

BASE = Path("/opt/neurodesktop")


def session():
    if os.geteuid() != 0:
        raise PermissionError("Glass session preparation requires root")
    if os.environ.get("NEURODESKTOP_RUNTIME") != "glass":
        raise ValueError("Glass requires NEURODESKTOP_RUNTIME=glass")
    account = pwd.getpwnam("jovyan")
    if (account.pw_uid, account.pw_gid) != (1000, 100):
        raise ValueError("CC Glass requires jovyan uid=1000 gid=100")
    runtime = Path("/run/user/1000")
    if runtime.is_symlink():
        raise ValueError("Refusing a symlink runtime directory")
    runtime.mkdir(parents=True, exist_ok=True)
    runtime.chmod(0o700)
    os.chown(runtime, 1000, 100, follow_symlinks=False)
    env = {**os.environ, "HOME": account.pw_dir, "USER": "jovyan",
           "NB_USER": "jovyan", "NB_UID": "1000", "NB_GID": "100",
           "XDG_RUNTIME_DIR": str(runtime), "DISPLAY": ":0",
           "XAUTHORITY": str(runtime / "neurodesktop/Xauthority")}
    # Reuse the current root/user hooks, including deferred Slurm, home defaults,
    # workspace quarantine and NBI configuration. No separate lite config tree.
    subprocess.run(["/bin/bash", "/usr/local/bin/before-notebook.d/before_notebook.sh"],
                   env=env, check=True)
    user_command = ["/usr/bin/setpriv", "--reuid=1000", "--regid=100", "--init-groups"]
    subprocess.run(user_command + ["/usr/bin/python3", "-I", str(BASE / "glass/jupyter.py"),
                                  "prepare"], env=env, check=True)
    # Never remove another live display's socket/lock. systemd tears down all
    # children on restart; Xorg owns cleanup of its display files.
    xorg = subprocess.Popen(["/usr/lib/xorg/Xorg", ":0", "-config",
                             str(BASE / "glass/xorg.conf"), "-auth", env["XAUTHORITY"],
                             "-noreset", "-nolisten", "tcp", "-novtswitch", "-sharevts"],
                            env=env)
    for _ in range(200):
        if xorg.poll() is not None:
            raise RuntimeError("Glass Xorg exited before display readiness; see journal")
        if Path("/tmp/.X11-unix/X0").is_socket():
            break
        time.sleep(0.05)
    else:
        raise TimeoutError("Glass Xorg did not create display :0")
    os.execvpe(user_command[0], user_command + ["/usr/bin/dbus-run-session", "--",
               "/bin/bash", "-lc",
               "source /opt/neurodesktop/environment_variables.sh; "
               "/usr/local/bin/glass-clipboard & /usr/local/bin/glass-resize & exec startlxde"], env)


if __name__ == "__main__":
    session()
