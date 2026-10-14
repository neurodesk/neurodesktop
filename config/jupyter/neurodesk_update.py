#!/usr/bin/python3 -I
import os
from pathlib import Path
import subprocess
import sys
import tempfile


CHECKOUT = Path("/neurocommand")


def install_update_launcher():
    directory = CHECKOUT / "local/bin"
    descriptor, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(
                '#!/bin/bash\n'
                '/usr/local/bin/neurodesk-update\n'
                'status=$?\n'
                'read -r -p "Press Enter to close this window."\n'
                'exit "$status"\n'
            )
            os.fchmod(stream.fileno(), 0o755)
        os.replace(temporary, directory / "update.sh")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update_neurocommand() -> int:
    if os.geteuid() == 0:
        raise PermissionError("Run the Neurodesk menu update as the notebook user, not root")
    if not all(os.access(path, os.W_OK | os.X_OK) for path in (
        CHECKOUT, CHECKOUT / ".git", CHECKOUT / "local/bin",
    )):
        raise PermissionError("The Neurodesk menu checkout is not writable in this session")
    try:
        return subprocess.run(["/bin/bash", "build.sh", "--update"], cwd=CHECKOUT).returncode
    finally:
        install_update_launcher()


def main():
    if len(sys.argv) != 1:
        print("Usage: neurodesk-update", file=sys.stderr)
        return 2
    try:
        return update_neurocommand()
    except OSError as error:
        print(f"Neurodesk menu update failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
