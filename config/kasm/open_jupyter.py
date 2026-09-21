#!/opt/conda/bin/python
"""Open the local Jupyter server with its current session token."""

import json
import subprocess
from urllib.parse import urlencode


def lab_url(server):
    return server["url"].rstrip("/") + "/lab?" + urlencode(
        {"token": server.get("token", "")}
    )


def main():
    result = subprocess.run(
        ["jupyter", "server", "list", "--json"],
        check=True, capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        server = json.loads(line)
        if server.get("port") == 8888:
            subprocess.run(["neurodesktop-firefox", lab_url(server)], check=True)
            return
    subprocess.run(["notify-send", "JupyterLab", "JupyterLab is still starting. Try again shortly."], check=True)


if __name__ == "__main__":
    main()
