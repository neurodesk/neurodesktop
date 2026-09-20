#!/usr/bin/python3 -I
"""Root startup policy. Never grant users sudo access to this program."""

import json
import os
from pathlib import Path
import pwd
import re
import secrets
import subprocess
import tempfile


SUDOERS = Path("/etc/sudoers.d")
STATE = Path("/var/lib/neurodesktop/rdp")
RUNTIME = Path("/run/neurodesktop/rdp")
XRDP_CONFIG = Path("/etc/xrdp/xrdp.ini")
ROOT_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LC_ALL": "C"}


def write_private(path, value, uid=0, gid=0):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(value)
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure_sudo(account, mode):
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*\$?", account.pw_name):
        raise ValueError("Unsupported notebook account name")
    mode = mode.lower()
    if mode not in {"packages", "yes", "y", "true", "1", "no", "n", "false", "0"}:
        raise ValueError("GRANT_SUDO must be packages, yes, or no")
    SUDOERS.mkdir(mode=0o755, exist_ok=True)
    # Remove the base image's grant too, including one left by an older startup.
    (SUDOERS / "added-by-start-script").unlink(missing_ok=True)
    policy = SUDOERS / "notebook"
    policy.unlink(missing_ok=True)
    if mode in {"no", "n", "false", "0"}:
        return
    if mode == "packages":
        rule = (
            f'Defaults:{account.pw_name} secure_path="/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"\n'
            f"{account.pw_name} ALL=(root) NOPASSWD: NOSETENV: /usr/local/bin/apt, /usr/local/bin/apt-get\n"
        )
    else:
        rule = f"{account.pw_name} ALL=(ALL) NOPASSWD:ALL\n"
    write_private(policy, rule)
    policy.chmod(0o440)
    try:
        subprocess.run(["/usr/sbin/visudo", "-cf", str(policy)], env=ROOT_ENV,
                       check=True, stdout=subprocess.DEVNULL)
    except BaseException:
        policy.unlink(missing_ok=True)
        raise


def configure_rdp(account, port):
    if not port.isascii() or not port.isdecimal() or not 1024 <= int(port) <= 65535:
        raise ValueError("NEURODESKTOP_RDP_PORT must be a port between 1024 and 65535")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    state_file = STATE / f"{account.pw_uid}.json"
    if state_file.exists():
        state = json.loads(state_file.read_text())
        if state.get("username") != account.pw_name or not re.fullmatch(r"[a-f0-9]{48}", state.get("password", "")):
            raise ValueError("Invalid saved RDP credential")
    else:
        state = {"username": account.pw_name, "password": secrets.token_hex(24)}
        write_private(state_file, json.dumps(state))
    subprocess.run(["/usr/sbin/chpasswd"],
                   input=f"{account.pw_name}:{state['password']}\n", text=True,
                   env=ROOT_ENV, check=True)
    runtime = RUNTIME / str(account.pw_uid)
    runtime.mkdir(mode=0o755, parents=True, exist_ok=True)
    # /run may be a fresh tmpfs and root's umask may be 077. The user needs
    # traversal to its own private files, without write access to the directory.
    for directory in (RUNTIME.parent, RUNTIME, runtime):
        directory.chmod(0o711)
    for name, value in {**state, "port": port}.items():
        write_private(runtime / name, value, account.pw_uid, account.pw_gid)
    config = XRDP_CONFIG.read_text()
    config, replacements = re.subn(
        r"(?m)^port=.*$", f"port=tcp://127.0.0.1:{port}", config, count=1,
    )
    if replacements != 1:
        raise ValueError("xrdp configuration has no listener port")
    XRDP_CONFIG.write_text(config)
    subprocess.run(["/usr/sbin/service", "xrdp", "start"], env=ROOT_ENV, check=True)


def main():
    if os.geteuid() != 0:
        raise PermissionError("Security initialization requires root")
    account = pwd.getpwnam(os.environ.get("NB_USER", "jovyan"))
    configure_sudo(account, os.environ.get("GRANT_SUDO", "packages"))
    Path("/run/sshd").mkdir(mode=0o755, exist_ok=True)
    configure_rdp(account, os.environ.get("NEURODESKTOP_RDP_PORT", "3389"))


if __name__ == "__main__":
    main()
