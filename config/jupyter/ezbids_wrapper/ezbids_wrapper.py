#!/usr/bin/env python3
"""
ezBIDS Wrapper Server

This server provides instant startup for ezBIDS by:
1. Immediately binding to port 3000 and serving a splash page
2. Starting the actual ezBIDS container in the background
3. Proxying requests to ezBIDS once it's ready
"""

import http.server
import socketserver
import subprocess
import threading
import urllib.request
import urllib.error
import os
import json
import time
import signal
import sys
from pathlib import Path

# Configuration
LISTEN_PORT = 13000  # Wrapper listens here (jupyter proxies to this)
EZBIDS_PORT = 3000   # ezBIDS runs on its default port
SCRIPT_DIR = Path(__file__).parent
SPLASH_PAGE = SCRIPT_DIR / "splash.html"
LOGFILE = "/tmp/ezbids_wrapper.log"

# Global state
container_ready = False
container_error = None
container_process = None
startup_start_time = None


def log(message):
    """Log message to file with timestamp."""
    with open(LOGFILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {message}\n")


def check_ezbids_ready():
    """Check if ezBIDS is responding on its port."""
    try:
        req = urllib.request.Request(
            f"http://localhost:{EZBIDS_PORT}/ezbids/",
            method="GET"
        )
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status == 200
    except (urllib.error.URLError, Exception):
        return False


def start_ezbids_container():
    """Start the ezBIDS container in background."""
    global container_ready, container_error, container_process, startup_start_time

    startup_start_time = time.time()
    log("Starting ezBIDS container...")

    try:
        # Check for local test image first
        local_sif = "/opt/ezbids_test/ezbids.sif"

        if os.path.exists(local_sif):
            log(f"Using local test image: {local_sif}")
            cmd = ["apptainer", "run", "--writable-tmpfs", local_sif]
        else:
            log("Using CVMFS module system")
            # Create a shell script to handle module loading
            cmd = [
                "bash", "-c",
                """
                source /usr/share/module.sh 2>/dev/null
                source /opt/neurodesktop/environment_variables.sh 2>/dev/null
                export neurodesk_singularity_opts=" --writable-tmpfs "
                ml ezbids 2>/dev/null
                ezbids start
                """
            ]

        container_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        # Wait for container to be ready (with timeout)
        max_wait = 300  # 5 minutes
        start_time = time.time()

        while time.time() - start_time < max_wait:
            if container_process.poll() is not None:
                # Process exited
                output = container_process.stdout.read() if container_process.stdout else ""
                container_error = f"Container exited unexpectedly: {output}"
                log(container_error)
                return

            if check_ezbids_ready():
                container_ready = True
                elapsed = time.time() - startup_start_time
                log(f"ezBIDS is ready! Startup took {elapsed:.1f}s")
                return

            time.sleep(1)

        container_error = "Timeout waiting for ezBIDS to start"
        log(container_error)

    except Exception as e:
        container_error = str(e)
        log(f"Error starting container: {e}")


class EzBIDSHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that serves splash page or proxies to ezBIDS."""

    def log_message(self, format, *args):
        """Override to log to file instead of stderr."""
        log(f"HTTP: {format % args}")

    def do_GET(self):
        self._handle_request("GET")

    def do_POST(self):
        self._handle_request("POST")

    def do_PUT(self):
        self._handle_request("PUT")

    def do_DELETE(self):
        self._handle_request("DELETE")

    def do_HEAD(self):
        self._handle_request("HEAD")

    def do_OPTIONS(self):
        self._handle_request("OPTIONS")

    def _handle_request(self, method):
        # Status endpoint for splash page polling
        # Handle both /ezbids-wrapper-status and /ezbids/ezbids-wrapper-status
        if self.path.endswith("/ezbids-wrapper-status"):
            self._send_status()
            return

        # If container is ready, proxy the request
        if container_ready:
            self._proxy_request(method)
            return

        # Otherwise serve splash page for GET requests to root-ish paths
        # Handle /, /ezbids, /ezbids/, etc.
        if method == "GET" and (self.path == "/" or self.path.rstrip("/") == "/ezbids"):
            self._serve_splash()
            return

        # For other requests while loading, return 503
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", "5")
        self.end_headers()
        self.wfile.write(json.dumps({
            "error": "ezBIDS is still starting up",
            "status": "loading"
        }).encode())

    def _send_status(self):
        """Send current status as JSON."""
        elapsed = time.time() - startup_start_time if startup_start_time else 0

        status = {
            "ready": container_ready,
            "error": container_error,
            "elapsed_seconds": round(elapsed, 1)
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(json.dumps(status).encode())

    def _serve_splash(self):
        """Serve the splash page."""
        try:
            with open(SPLASH_PAGE, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Loading ezBIDS...</h1><script>setTimeout(()=>location.reload(), 3000)</script></body></html>")

    def _proxy_request(self, method):
        """Proxy request to the actual ezBIDS server."""
        try:
            # Build the target URL
            target_url = f"http://localhost:{EZBIDS_PORT}{self.path}"

            # Read request body if present
            content_length = self.headers.get("Content-Length")
            body = None
            if content_length:
                body = self.rfile.read(int(content_length))

            # Create the proxy request
            req = urllib.request.Request(target_url, data=body, method=method)

            # Copy relevant headers
            for header, value in self.headers.items():
                if header.lower() not in ("host", "content-length"):
                    req.add_header(header, value)

            # Make the request
            response = urllib.request.urlopen(req, timeout=300)

            # Send response back
            self.send_response(response.status)
            for header, value in response.getheaders():
                if header.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(header, value)
            self.end_headers()

            # Stream response body
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)

        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for header, value in e.headers.items():
                if header.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(header, value)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            log(f"Proxy error: {e}")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Proxy error: {e}".encode())


def signal_handler(_sig, _frame):
    """Handle shutdown signals."""
    log("Received shutdown signal")
    if container_process:
        container_process.terminate()
    sys.exit(0)


def main():
    log("=" * 50)
    log("ezBIDS Wrapper Server starting")

    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start the container in a background thread
    container_thread = threading.Thread(target=start_ezbids_container, daemon=True)
    container_thread.start()

    # Start the HTTP server immediately
    with socketserver.TCPServer(("", LISTEN_PORT), EzBIDSHandler) as httpd:
        log(f"Serving on port {LISTEN_PORT}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
