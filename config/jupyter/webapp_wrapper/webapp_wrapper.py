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
import urllib.parse
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

        # Path rewrites for apps built with hard-coded absolute paths
        # This rewrites paths like /hub/ezbids/ to the correct base path
        # Always include the app's own path as a fallback rewrite
        self.path_rewrites = config.get("path_rewrites", [])
        # Add default rewrite for the app's own path (e.g., /ezbids/ -> base_path)
        if f"/{self.app_name}/" not in self.path_rewrites:
            self.path_rewrites.append(f"/{self.app_name}/")

        # Paths
        self.logfile = f"/tmp/{self.app_name}_wrapper.log"
        self.status_endpoint = f"{self.app_name}-wrapper-status"


# Global state
config: WebappConfig = None
container_ready = False
container_error = None
container_process = None
container_output = []  # Collected output from container process
startup_start_time = None


def drain_process_output(proc):
    """Read and store process output to prevent pipe buffer from blocking."""
    global container_output
    try:
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                container_output.append(line.rstrip())
                log(f"Container output: {line.rstrip()}")
    except Exception as e:
        log(f"Error draining output: {e}")


def log(message):
    """Log message to file with timestamp."""
    with open(config.logfile, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: {message}\n")


def check_app_ready():
    """Check if the webapp is responding on its port."""
    try:
        # Just check if something is listening and responding
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('localhost', config.target_port))
        sock.close()
        return result == 0
    except Exception:
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
            log(f"Startup command: {config.startup_command}")
            # Bind mount storage and home directories so apps can access user files
            cmd = [
                "apptainer", "exec", "--writable-tmpfs",
                "-B", "/neurodesktop-storage:/neurodesktop-storage",
                "-B", "/home/jovyan:/home/jovyan",
                local_sif, config.startup_command
            ]
            log(f"Full command: {cmd}")
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

        # Start a thread to drain output and prevent pipe buffer from blocking
        output_thread = threading.Thread(
            target=drain_process_output,
            args=(container_process,),
            daemon=True
        )
        output_thread.start()

        # Wait for container to be ready (with timeout)
        start_time = time.time()

        while time.time() - start_time < config.startup_timeout:
            # Check if app is ready FIRST (rserver may fork and parent exits)
            if check_app_ready():
                container_ready = True
                elapsed = time.time() - startup_start_time
                log(f"{config.app_name} is ready! Startup took {elapsed:.1f}s")
                return

            poll_result = container_process.poll()
            if poll_result is not None:
                # Process exited - but check multiple times if app is ready
                # (some apps like rserver fork and the parent exits immediately)
                log(f"Process exited with code: {poll_result}, checking if app started anyway...")
                for retry in range(10):
                    time.sleep(1)
                    if check_app_ready():
                        container_ready = True
                        elapsed = time.time() - startup_start_time
                        log(f"{config.app_name} is ready! (process exited but app responding) Startup took {elapsed:.1f}s")
                        return
                    log(f"Retry {retry + 1}/10: app not ready yet")
                # Process exited and app not ready after retries - get collected output
                output = "\n".join(container_output) if container_output else "(no output)"
                container_error = f"Container exited unexpectedly: {output}"
                log(container_error)
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

    def _get_normalized_path(self):
        """
        Normalize the request path by stripping JupyterHub prefix if present.

        On JupyterHub, paths come through as /user/<username>/<app>/...
        We need to strip the /user/<username>/ prefix to get /<app>/...

        On localhost, paths are already /<app>/... so no change needed.
        """
        # Parse to handle any query strings
        parsed_path = urllib.parse.urlparse(self.path).path

        # Look for the app name in the path and extract from there
        app_marker = f"/{config.app_name}"
        idx = parsed_path.find(app_marker)
        if idx != -1:
            # Found app name - return path starting from there
            return parsed_path[idx:]

        # No app name found - return path as-is (handles / and empty paths)
        return parsed_path

    def _is_root_path(self):
        """Check if path is a root-like path for the app."""
        path = self._get_normalized_path().rstrip("/")
        return path == "" or path == "/" or path == f"/{config.app_name}"

    def _is_main_app_html(self):
        """Check if this is a request for the main app HTML page."""
        path = self._get_normalized_path().rstrip("/")
        return path == f"/{config.app_name}" or path == f"/{config.app_name}/index.html"

    def _get_base_path(self):
        """
        Get the full base path for the app, including any JupyterHub prefix.

        On JupyterHub: /user/<username>/<app_name>/
        On localhost: /<app_name>/

        Returns the path ending with a trailing slash.
        """
        parsed_path = urllib.parse.urlparse(self.path).path
        app_marker = f"/{config.app_name}"
        idx = parsed_path.find(app_marker)
        if idx != -1:
            # Return everything up to and including the app name, plus trailing slash
            return parsed_path[:idx + len(app_marker)] + "/"
        # Fallback to just the app name
        return f"/{config.app_name}/"

    def _rewrite_location(self, location, target_port):
        """Rewrite Location header to go through proxy instead of direct port access."""
        # Rewrite http://localhost:PORT/path to base_path + path
        import re
        pattern = rf"^https?://(?:localhost|127\.0\.0\.1):{target_port}(/.*)?$"
        match = re.match(pattern, location)
        if match:
            path = match.group(1) or "/"
            base_path = self._get_base_path().rstrip("/")
            return f"{base_path}{path}"
        return location

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
            # Use normalized path for route matching (handles JupyterHub prefix)
            normalized_path = self._get_normalized_path()
            target_port = config.target_port  # default
            is_main_html = self._is_main_app_html()

            # Preserve query string from original request
            parsed_url = urllib.parse.urlparse(self.path)
            query_string = f"?{parsed_url.query}" if parsed_url.query else ""

            # Check routes and strip prefixes (apps listen at / not /appname)
            path = normalized_path  # default to normalized path
            for prefix, port in config.routes:
                if normalized_path.startswith(prefix):
                    path = normalized_path[len(prefix):] or "/"
                    target_port = port
                    break

            # Build the target URL (add back query string)
            target_url = f"http://localhost:{target_port}{path}{query_string}"

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

            # Make the request - use custom opener that doesn't follow redirects
            # so we can rewrite Location headers
            class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None  # Don't follow redirects

            opener = urllib.request.build_opener(NoRedirectHandler)
            try:
                response = opener.open(req, timeout=300)
            except urllib.error.HTTPError as redirect_error:
                # 3xx redirects come as HTTPError when not followed
                if 300 <= redirect_error.code < 400:
                    response = redirect_error  # Use the redirect response
                else:
                    raise

            # Check content type for response handling
            content_type = response.getheader("Content-Type", "")

            # Determine what processing is needed:
            # - Path rewriting: for text-based responses (HTML, JS, CSS) that may contain hard-coded paths
            # - Base href injection: only for main HTML page
            needs_path_rewrite = (
                config.path_rewrites and
                any(ct in content_type for ct in ["text/html", "text/javascript", "application/javascript", "text/css"])
            )
            needs_base_href = is_main_html and "text/html" in content_type

            if needs_path_rewrite or needs_base_href:
                # Read full response to modify
                response_body = response.read()

                # Get the full base path including any JupyterHub prefix
                base_path = self._get_base_path()

                # Rewrite hard-coded absolute paths to the correct base path
                # This fixes apps built with paths like /hub/ezbids/ or /ezbids/
                if needs_path_rewrite:
                    for rewrite_path in config.path_rewrites:
                        # Rewrite paths in HTML/JS/CSS
                        # The path_rewrite is like "/hub/ezbids/" and should become base_path
                        old_bytes = rewrite_path.encode('utf-8')
                        new_bytes = base_path.encode('utf-8')
                        response_body = response_body.replace(old_bytes, new_bytes)

                # Inject base href and routing fix for main HTML pages
                if needs_base_href:
                    # The base href fixes relative URL resolution (assets, etc.)
                    # Since we can't patch location.pathname, we strip the base path from
                    # the URL entirely. The app works during the session, but refresh on
                    # sub-pages will redirect to JupyterLab (the server doesn't know the context).
                    # This is a limitation of apps not built with proper base path support.
                    inject_script = f'''<base href="{base_path}">
<script>
(function() {{
  var basePath = '{base_path.rstrip("/")}';
  var origReplace = History.prototype.replaceState;

  // Strip base path from current URL so router sees correct path
  // This must happen BEFORE React/Vue/etc initializes
  var currentPath = window.location.pathname;
  var newPath = currentPath;
  if (currentPath === basePath || currentPath === basePath + '/') {{
    newPath = '/';
  }} else if (currentPath.startsWith(basePath + '/')) {{
    newPath = currentPath.substring(basePath.length);
  }}
  if (newPath !== currentPath) {{
    origReplace.call(history, history.state, '', newPath + window.location.search + window.location.hash);
  }}

  // Don't patch pushState/replaceState - let the router navigate freely
  // The URL will be like /workspace instead of /dicompare/workspace
  // This means refresh won't work on sub-pages, but the app works during session
}})();
</script>'''
                    inject_bytes = inject_script.encode('utf-8')
                    if b'<head>' in response_body:
                        response_body = response_body.replace(b'<head>', b'<head>' + inject_bytes, 1)
                    elif b'<HEAD>' in response_body:
                        response_body = response_body.replace(b'<HEAD>', b'<HEAD>' + inject_bytes, 1)

                # Send modified response
                status = response.status if hasattr(response, 'status') else response.code
                headers = response.getheaders() if hasattr(response, 'getheaders') else response.headers.items()

                self.send_response(status)
                for header, value in headers:
                    if header.lower() not in ("transfer-encoding", "connection", "content-length"):
                        # Rewrite Location headers to go through proxy
                        if header.lower() == "location":
                            value = self._rewrite_location(value, target_port)
                        self.send_header(header, value)
                self.send_header("Content-Length", len(response_body))
                self.end_headers()
                self.wfile.write(response_body)
            else:
                # Stream response as-is
                # Handle both normal responses and HTTPError (for redirects)
                status = response.status if hasattr(response, 'status') else response.code
                headers = response.getheaders() if hasattr(response, 'getheaders') else response.headers.items()

                self.send_response(status)
                for header, value in headers:
                    if header.lower() not in ("transfer-encoding", "connection"):
                        # Rewrite Location headers to go through proxy
                        if header.lower() == "location":
                            value = self._rewrite_location(value, target_port)
                        self.send_header(header, value)
                self.end_headers()

                # For redirects, there may be minimal body
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        except urllib.error.HTTPError as e:
            try:
                self.send_response(e.code)
                for header, value in e.headers.items():
                    if header.lower() not in ("transfer-encoding", "connection"):
                        self.send_header(header, value)
                self.end_headers()
                # Read the response body (may be empty for 304)
                body = e.read()
                if body:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                log(f"Client disconnected while sending HTTP {e.code} response")
        except (BrokenPipeError, ConnectionResetError):
            log("Client disconnected during proxying")
        except Exception as e:
            log(f"Proxy error: {e}")
            try:
                self.send_response(502)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(f"Proxy error: {e}".encode())
            except (BrokenPipeError, ConnectionResetError):
                log("Client disconnected before error response could be sent")


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
