"""Wait for a live Jupyter server's local HTTP endpoint before deferred work."""

import json
import os
from pathlib import Path
import re
import time
from urllib import error, request


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def runtime_directories():
    directories = [
        Path.home() / ".local/share/jupyter/runtime",
        Path("/home") / os.environ.get("NB_USER", "jovyan") / ".local/share/jupyter/runtime",
    ]
    if os.environ.get("JUPYTER_RUNTIME_DIR"):
        directories.insert(0, Path(os.environ["JUPYTER_RUNTIME_DIR"]))
    return list(dict.fromkeys(directories))


def server_urls(directories):
    for directory in directories:
        for path in directory.glob("jpserver-*.json"):
            match = re.fullmatch(r"jpserver-([1-9][0-9]*)\.json", path.name)
            if match is None:
                continue
            try:
                os.kill(int(match[1]), 0)
                info = json.loads(path.read_text())
                port = info["port"]
                if type(port) is not int or not 1 <= port <= 65535:
                    continue
                base = info.get("base_url") or "/"
                if not isinstance(base, str) or not base.startswith("/"):
                    continue
                scheme = "https" if info.get("secure") else "http"
                # Probe locally without sending the runtime file's token.
                yield f"{scheme}://127.0.0.1:{port}{base}"
            except (OSError, ValueError, KeyError, TypeError):
                continue


def wait_for_jupyter(timeout=120):
    deadline = time.monotonic() + timeout
    opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
    directories = runtime_directories()
    while time.monotonic() < deadline:
        for url in server_urls(directories):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                with opener.open(url, timeout=min(2, remaining)) as response:
                    if 200 <= response.status < 400:
                        return True
            except error.HTTPError as exc:
                # Authentication redirects and denials also prove HTTP readiness.
                ready = 300 <= exc.code < 400 or exc.code in (401, 403)
                exc.close()
                if ready:
                    return True
            except (OSError, ValueError):
                pass
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    return False


if __name__ == "__main__":
    raise SystemExit(0 if wait_for_jupyter() else 1)
