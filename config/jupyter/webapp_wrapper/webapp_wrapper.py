#!/usr/bin/env python3
"""
Generic Webapp Wrapper Server for Neurodesk

This server provides instant startup for web applications by:
1. Immediately binding to the wrapper port and serving a splash page
2. Starting the actual container application in the background
3. Proxying requests to the application once it's ready

Usage: webapp_wrapper.py <app_name>

Reads configuration from /opt/neurodesktop/webapps.json
"""

import http.server
import socket
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
from string import Template


# Paths
CONFIG_PATH = Path("/opt/neurodesktop/webapps.json")
SCRIPT_DIR = Path(__file__).parent
SPLASH_TEMPLATE_PATH = SCRIPT_DIR / "splash_template.html"


class UnixSocketHTTPServer(socketserver.UnixStreamServer):
    """HTTP server that listens on a Unix socket."""

    def get_request(self):
        request, client_address = super().get_request()
        # Wrap the socket to work with HTTP handler
        return request, ("", 0)


class WebappConfig:
    """Load and provide access to webapp configuration."""

    def __init__(self, app_name: str):
        self.app_name = app_name
        self._load_config()

    def _load_config(self):
        if not CONFIG_PATH.exists():
            raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")

        with open(CONFIG_PATH) as f:
            all_config = json.load(f)

        webapps = all_config.get("webapps", {})
        if self.app_name not in webapps:
            available = ", ".join(webapps.keys()) or "none"
            raise ValueError(f"Unknown webapp: {self.app_name}. Available: {available}")

        config = webapps[self.app_name]

        # Extract configuration
        self.title = config.get("title", self.app_name)
        self.module = config.get("module", self.app_name)
        self.version = config.get("version")  # Container version for module loading
        self.startup_command = config.get("startup_command", f"{self.app_name} start")
        self.description = config.get("description", "")

        # Unix socket path - deterministic from app name
        self.socket_path = f"/tmp/neurodesk_webapp_{self.app_name}.sock"

        # Target port (where the actual app listens)
        self.target_port = config.get("port", 3000)

        # Build routing table: list of (path_prefix, target_port) tuples
        # More specific routes (longer prefixes) should be checked first
        self.routes = []

        # Additional proxies (e.g., API endpoints)
        for proxy in config.get("additional_proxies", []):
            prefix = f"/{proxy['path']}"
            port = proxy['port']
            self.routes.append((prefix, port))

        # Main app route (least specific, checked last)
        self.routes.append((f"/{self.app_name}", self.target_port))

        # Sort by prefix length descending (most specific first)
        self.routes.sort(key=lambda x: len(x[0]), reverse=True)

        # Startup check
        self.start_page = config.get("start_page", "/")
        self.startup_timeout = config.get("startup_timeout", 120)

        # Paths
        self.logfile = f"/tmp/{self.app_name}_wrapper.log"
        self.status_endpoint = f"{self.app_name}-wrapper-status"


# Global state
config: WebappConfig = None
container_ready = False
container_error = None
container_process = None
startup_start_time = None


def log(message):
    """Log message to file with timestamp."""
    with open(config.logfile, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {message}\n")


def check_app_ready():
    """Check if the webapp is responding on its port."""
    try:
        url = f"http://localhost:{config.target_port}{config.start_page}"
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status == 200
    except (urllib.error.URLError, Exception):
        return False


def start_container():
    """Start the webapp container in background."""
    global container_ready, container_error, container_process, startup_start_time

    startup_start_time = time.time()
    log(f"Starting {config.app_name} container...")

    try:
        # Check for local test image first (mounted via build_and_run.sh)
        local_sif = f"/opt/neurodesktop-test-webapps/{config.app_name}/{config.app_name}.sif"

        if os.path.exists(local_sif):
            log(f"Using local test image: {local_sif}")
            cmd = ["apptainer", "run", "--writable-tmpfs", local_sif]
        else:
            log("Using CVMFS module system")
            # Build module spec with version if available
            module_spec = f"{config.module}/{config.version}" if config.version else config.module
            log(f"Loading module: {module_spec}")
            # Create a shell script to handle module loading
            cmd = [
                "bash", "-c",
                f"""
                source /usr/share/module.sh 2>/dev/null
                source /opt/neurodesktop/environment_variables.sh 2>/dev/null
                export neurodesk_singularity_opts=" --writable-tmpfs "
                ml {module_spec} 2>/dev/null
                {config.startup_command}
                """
            ]

        container_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        # Wait for container to be ready (with timeout)
        start_time = time.time()

        while time.time() - start_time < config.startup_timeout:
            if container_process.poll() is not None:
                # Process exited
                output = container_process.stdout.read() if container_process.stdout else ""
                container_error = f"Container exited unexpectedly: {output}"
                log(container_error)
                return

            if check_app_ready():
                container_ready = True
                elapsed = time.time() - startup_start_time
                log(f"{config.app_name} is ready! Startup took {elapsed:.1f}s")
                return

            time.sleep(1)

        container_error = f"Timeout waiting for {config.app_name} to start"
        log(container_error)

    except Exception as e:
        container_error = str(e)
        log(f"Error starting container: {e}")


def render_splash_template():
    """Render the splash page template with app-specific values."""
    try:
        with open(SPLASH_TEMPLATE_PATH, 'r') as f:
            template = Template(f.read())

        return template.safe_substitute(
            app_name=config.app_name,
            app_title=config.title,
            app_description=config.description or f"Loading {config.title}...",
            status_endpoint=config.status_endpoint
        ).encode('utf-8')
    except FileNotFoundError:
        # Fallback if template is missing
        return f"""<!DOCTYPE html>
<html><head><title>Loading {config.title}...</title></head>
<body style="font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #1a1a2e; color: white;">
<div style="text-align: center;">
<h1>{config.title}</h1>
<p>Loading...</p>
<script>setTimeout(() => location.reload(), 3000)</script>
</div></body></html>""".encode('utf-8')


class WebappHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that serves splash page or proxies to webapp."""

    def log_message(self, format, *args):
        """Override to log to file instead of stderr."""
        log(f"HTTP: {format % args}")

    def do_GET(self):
        self._handle_request("GET")

    def do_POST(self):
        self._handle_request("POST")

    def do_PUT(self):
        self._handle_request("PUT")

    def do_PATCH(self):
        self._handle_request("PATCH")

    def do_DELETE(self):
        self._handle_request("DELETE")

    def do_HEAD(self):
        self._handle_request("HEAD")

    def do_OPTIONS(self):
        self._handle_request("OPTIONS")

    def _handle_request(self, method):
        # Status endpoint for splash page polling
        if self.path.endswith(f"/{config.status_endpoint}"):
            self._send_status()
            return

        # If container is ready, proxy all requests
        if container_ready:
            self._proxy_request(method)
            return

        # Otherwise serve splash page for GET requests to root-ish paths
        if method == "GET" and self._is_root_path():
            self._serve_splash()
            return

        # For other requests while loading, return 503
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", "5")
        self.end_headers()
        self.wfile.write(json.dumps({
            "error": f"{config.title} is still starting up",
            "status": "loading"
        }).encode())

    def _is_root_path(self):
        """Check if path is a root-like path for the app."""
        path = self.path.rstrip("/")
        return path == "" or path == "/" or path == f"/{config.app_name}"

    def _is_main_app_html(self):
        """Check if this is a request for the main app HTML page."""
        path = self.path.rstrip("/")
        return path == f"/{config.app_name}" or path == f"/{config.app_name}/index.html"

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
        content = render_splash_template()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(content))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def _proxy_request(self, method):
        """Proxy request to the actual webapp server."""
        try:
            # Find matching route and determine target port
            path = self.path
            target_port = config.target_port  # default
            is_main_html = self._is_main_app_html()

            # Check routes and strip prefixes
            for prefix, port in config.routes:
                if path.startswith(prefix):
                    path = path[len(prefix):] or "/"
                    target_port = port
                    break

            # Build the target URL
            target_url = f"http://localhost:{target_port}{path}"

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

            # Check if we need to inject URL rewriting script (for main HTML page)
            content_type = response.getheader("Content-Type", "")
            should_inject = (
                is_main_html and
                "text/html" in content_type
            )

            if should_inject:
                # Read full response to inject script
                response_body = response.read()

                # Inject base href to preserve relative URL resolution,
                # plus script to rewrite URL for client-side routing
                inject_script = f'''<base href="/{config.app_name}/">
<script>
(function() {{
  // Rewrite URL for client-side routing frameworks (React Router, etc.)
  // They see the full path and need it to appear as "/" for proper routing
  var appPath = '/{config.app_name}';
  if (window.location.pathname === appPath || window.location.pathname === appPath + '/') {{
    window.history.replaceState(null, '', '/' + window.location.search + window.location.hash);
  }}
}})();
</script>'''

                # Inject after <head> tag
                inject_bytes = inject_script.encode('utf-8')
                if b'<head>' in response_body:
                    response_body = response_body.replace(b'<head>', b'<head>' + inject_bytes, 1)
                elif b'<HEAD>' in response_body:
                    response_body = response_body.replace(b'<HEAD>', b'<HEAD>' + inject_bytes, 1)

                # Send modified response
                self.send_response(response.status)
                for header, value in response.getheaders():
                    if header.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(header, value)
                self.send_header("Content-Length", len(response_body))
                self.end_headers()
                self.wfile.write(response_body)
            else:
                # Stream response as-is
                self.send_response(response.status)
                for header, value in response.getheaders():
                    if header.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(header, value)
                self.end_headers()

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
    global config

    if len(sys.argv) != 2:
        print("Usage: webapp_wrapper.py <app_name>")
        print()
        print("Starts a wrapper server for the specified webapp.")
        print("Configuration is read from /opt/neurodesktop/webapps.json")
        sys.exit(1)

    app_name = sys.argv[1]

    try:
        config = WebappConfig(app_name)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    log("=" * 50)
    log(f"{config.title} Wrapper Server starting")
    log(f"  Socket: {config.socket_path}")
    log(f"  Target port: {config.target_port}")
    log(f"  Start page: {config.start_page}")

    # Set up signal handlers early
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Remove existing socket file if present (needed to bind)
    socket_path = config.socket_path
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    # Create and bind to socket
    httpd = UnixSocketHTTPServer(socket_path, WebappHandler)
    os.chmod(socket_path, 0o666)
    log(f"Bound to Unix socket: {socket_path}")

    # Start the container in a background thread
    container_thread = threading.Thread(target=start_container, daemon=True)
    container_thread.start()

    # Serve requests
    log(f"Serving on Unix socket: {socket_path}")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
