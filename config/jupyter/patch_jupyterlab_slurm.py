#!/usr/bin/env python3
"""Use the Slurm subprocess identity in Neurodesktop's single-user server.

JupyterHub logins and token-generated Jupyter identities need not be Unix
accounts. Slurm executes as the server's effective UID, including in host
Slurm mode, so its dashboard filters must use that account instead.
"""

from pathlib import Path
import sys


BEFORE = """            # Prefer the authenticated Jupyter identity (works correctly under
            # multi-user deployments, e.g. JupyterHub, where the OS process
            # `USER` env var may be shared, unset, or belong to a service
            # account rather than the actual signed-in user). Fall back to the
            # process `USER` only for single-user/local deployments where no
            # Jupyter identity is configured.
            username = None
            current_user = getattr(self, "current_user", None)
            if current_user is not None:
                username = getattr(current_user, "username", None) or getattr(current_user, "name", None)
                if username is None and isinstance(current_user, str):
                    username = current_user
            if not username:
                username = os.environ.get('USER')
"""

AFTER = """            # neurodesktop-slurm-process-identity
            # The authenticated Hub name need not exist in Slurm's OS namespace.
            # Resolve the account that actually executes our Slurm subprocesses.
            import pwd
            username = pwd.getpwuid(os.geteuid()).pw_name
"""


def patch_handler(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if source.count(AFTER) == 1 and BEFORE not in source:
        return False
    if source.count(BEFORE) != 1 or "neurodesktop-slurm-process-identity" in source:
        raise ValueError("Slurm identity anchor changed; reassess the username patch")
    patched = source.replace(BEFORE, AFTER)
    compile(patched, str(path), "exec")
    path.write_text(patched, encoding="utf-8")
    return True


if __name__ == "__main__":
    patch_handler(Path(sys.argv[1]))
