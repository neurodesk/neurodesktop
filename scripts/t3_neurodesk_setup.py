#!/usr/bin/env python3
"""Interactive, unprivileged T3/Tailscale setup. No third-party Python dependencies."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request


DEVICE_NAME = r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+\.ts\.net"


class SetupError(Exception):
    pass


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.geteuid():
        raise SetupError(f"Use a directory owned by your notebook user: {path}")
    path.chmod(0o700)


def command_json(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        raise SetupError("Tailscale's local API did not respond within five seconds.") from None
    # Do not echo subprocess output: status can contain an authentication URL.
    if result.returncode:
        raise SetupError("Tailscale's local API is unavailable. Check the daemon and socket path.")
    try:
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except ValueError:
        raise SetupError("Tailscale returned an invalid status response.") from None


def interactive(command, timeout=600):
    # Inherit the terminal; never capture or save login links or pairing tokens.
    result = subprocess.run(command, timeout=timeout)
    if result.returncode:
        raise SetupError("The command did not complete. Follow its instructions, then rerun setup.")


def t3_environment(port):
    url = f"http://127.0.0.1:{port}/.well-known/t3/environment"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=5) as response:
            data = json.loads(response.read(65536))
        if not isinstance(data.get("environmentId"), str) or not data["environmentId"]:
            raise ValueError()
        return data
    except (OSError, ValueError, AttributeError, urllib.error.URLError):
        raise SetupError(
            f"T3 is not responding at {url}. Wait for Jupyter to start and rerun setup. "
            "T3 starts automatically with Jupyter; if it remains unavailable, "
            "check the Jupyter server log. "
            "For a custom T3 port, use --port."
        ) from None


def ensure_daemon(tailscaled, ts, socket, state_dir):
    if socket.exists():
        return command_json([*ts, "status", "--json"])
    private_directory(state_dir)
    print("Starting a Tailscale daemon; it keeps running after this terminal closes.")
    process = subprocess.Popen(
        [tailscaled, "--tun=userspace-networking", "--port=0",
         f"--socket={socket}", f"--statedir={state_dir}",
         f"--state={state_dir / 'tailscaled.state'}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            if socket.exists():
                try:
                    status = command_json([*ts, "status", "--json"])
                    if status.get("BackendState") not in {None, "NoState", "Starting"}:
                        return status
                except SetupError:
                    # The socket can exist before the LocalAPI is ready.
                    pass
            time.sleep(0.1)
        raise SetupError(
            "Tailscale did not start. Check that your state directory is writable and "
            "not used by another instance. Run tailscaled in the foreground to see its diagnostics."
        )
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        raise


def device_host(status):
    if status.get("BackendState") != "Running":
        raise SetupError(
            "Tailscale is not connected yet. Complete login/device approval in the "
            "Tailscale admin console, check outbound connectivity, then rerun setup."
        )
    hostname = (status.get("Self") or {}).get("DNSName") or ""
    hostname = hostname.rstrip(".")
    if not re.fullmatch(DEVICE_NAME, hostname):
        raise SetupError("No complete device DNS name is available. Enable MagicDNS in your tailnet.")
    return hostname


def configured_serve_host(config, hostname, target):
    """Return the reachable T3 endpoint, plus endpoints this device cannot answer."""
    if any(config.get("AllowFunnel", {}).values()):
        raise SetupError("Funnel is enabled. Disable public access before using this private setup.")
    listener = config.get("TCP", {}).get("443")
    endpoints = {
        key[:-4]: value for key, value in config.get("Web", {}).items()
        if key.endswith(":443")
    }
    if listener is None and not endpoints:
        return None, []
    if listener == {"HTTPS": True}:
        matches = [
            host for host, web in endpoints.items()
            if re.fullmatch(DEVICE_NAME, host)
            and web == {"Handlers": {"/": {"Proxy": target}}}
        ]
        if hostname in matches:
            return hostname, []
        if hostname not in endpoints and matches:
            # Serve keeps one handler per device name, and Tailscale answers
            # only on the name the device holds now. A handler under any other
            # name resolves nowhere, so report it as stale instead of
            # reusing it and handing the desktop an address it cannot reach.
            return None, sorted(matches)
    raise SetupError(
        f"Tailscale port 443 does not have a reusable HTTPS proxy to {target}. "
        "Setup has left it unchanged; review it with tailscale serve status."
    )


def stable_hostname(ts, status, desired):
    """Hold a tailnet name across container restarts.

    The container's own hostname is its container ID, so a recreated container
    joins the tailnet under a new name and every address printed by an earlier
    setup stops resolving. Naming the device once keeps its address stable.
    """
    def label(value):
        name = ((value.get("Self") or {}).get("DNSName") or "").split(".")[0]
        # Tailscale appends a suffix when the name is already taken; that
        # assigned name is itself stable, so keep it rather than renaming.
        return name if name == desired or re.fullmatch(rf"{re.escape(desired)}-\d+", name) else None

    if not desired or label(status):
        return status
    print(f"Naming this device {desired} so its address survives a container restart.")
    interactive([*ts, "set", f"--hostname={desired}"], timeout=30)
    deadline = time.monotonic() + 15
    while True:
        status = command_json([*ts, "status", "--json"])
        if label(status):
            return status
        if time.monotonic() >= deadline:
            raise SetupError(
                f"Tailscale did not accept the device name {desired}. "
                "Set it in the Tailscale admin console, then rerun setup."
            )
        time.sleep(0.5)


def describe_stale(stale):
    return (f"Tailscale Serve still lists {', '.join(stale)}, "
            "which this device no longer answers to.")


def pairing_url(t3, base_dir, endpoint):
    """Mint a one-time pairing link for the address the desktop can reach.

    `t3 pair` builds its link and QR code from the server's own address, which
    is the container's, so neither reaches the desktop. Ask T3 to build the
    link against the tailnet address this setup configured instead. Pasting
    that link into the desktop app's Host field fills the host and the pairing
    code together. The link stays in memory and in the terminal: never in an
    error message, a log, or a file.
    """
    command = [t3, "--log-level=warn", "auth", "pairing", "create",
               "--base-dir", str(base_dir), "--ttl", "5m", "--label", "Desktop app",
               "--base-url", endpoint, "--json"]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise SetupError("T3 did not issue a pairing code. Rerun setup.") from None
    if result.returncode:
        raise SetupError("T3 could not issue a pairing code. Check that the T3 server is running.")
    try:
        url = json.loads(result.stdout)["pairUrl"]
        if not url.startswith(endpoint + "/pair#"):
            raise ValueError()
        return url
    except (ValueError, KeyError, TypeError, AttributeError):
        raise SetupError("T3 returned an unexpected pairing response.") from None


def check_pairing_identity(base_dir, environment_id):
    try:
        identity = (base_dir / "userdata/environment-id").read_text().strip()
    except OSError:
        identity = None
    if identity != environment_id:
        raise SetupError(
            "The T3 state directory does not match the running server. "
            "Rerun with --base-dir set to that server's NEURODESKTOP_T3_CODE_HOME."
        )


def setup(args):
    if os.geteuid() == 0:
        raise SetupError("Run this in a JupyterLab terminal as the notebook user, without sudo.")
    if not args.check and not (sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()):
        raise SetupError("Run setup in an interactive terminal without output redirection. Use --check for diagnostics.")
    binaries = {name: shutil.which(name) for name in ("tailscale", "tailscaled", "t3")}
    missing = [name for name, path in binaries.items() if path is None]
    if missing:
        raise SetupError(f"Missing binaries: {', '.join(missing)}. Use a Neurodesktop image that includes Tailscale and T3.")
    print("1. Check the T3 server")
    descriptor = t3_environment(args.port)
    check_pairing_identity(args.base_dir, descriptor["environmentId"])
    print("T3 is responding.")
    ts = [binaries["tailscale"], f"--socket={args.socket}"]
    if args.check:
        if not args.socket.exists():
            raise SetupError("No Tailscale socket found. Run t3_neurodesk_setup interactively to start it.")
        host = device_host(command_json([*ts, "status", "--json"]))
        host, stale = configured_serve_host(
            command_json([*ts, "serve", "status", "--json"]), host, f"http://127.0.0.1:{args.port}"
        )
        if host is None:
            raise SetupError(
                describe_stale(stale) + " Rerun t3_neurodesk_setup to configure the current name."
                if stale else
                "Tailscale is connected but the T3 HTTPS proxy is not configured."
            )
        print(f"Private endpoint configured: https://{host}")
        print("This local check does not verify access from your desktop.")
        return

    private_directory(args.socket.parent)
    with (args.socket.parent / "setup.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError("Another setup is running. Finish it before starting this one.") from None
        print("\n2. Connect Tailscale")
        status = ensure_daemon(binaries["tailscaled"], ts, args.socket, args.state_dir)
        if status.get("BackendState") != "Running":
            print("Sign in to the tailnet your desktop uses, and complete any device approval.")
            print("Keep the login link below private. You have ten minutes to complete this step.")
            interactive([*ts, "up", "--accept-dns=false",
                         *([f"--hostname={args.tailscale_hostname}"] if args.tailscale_hostname else [])])
            status = command_json([*ts, "status", "--json"])
        status = stable_hostname(ts, status, args.tailscale_hostname)
        host = device_host(status)
        target = f"http://127.0.0.1:{args.port}"
        print("\n3. Configure private HTTPS access")
        existing_host, stale = configured_serve_host(
            command_json([*ts, "serve", "status", "--json"]), host, target
        )
        if existing_host is not None:
            host = existing_host
        else:
            if stale:
                print(describe_stale(stale) + f" Configuring {host} instead.")
                print("Setup leaves the old entry in place; clear it with tailscale serve reset when nothing else uses Serve.")
            print("If Tailscale asks you to enable HTTPS, follow its link. Then rerun setup if requested.")
            interactive([*ts, "serve", "--bg", "--https=443", target])
            configured_host, _ = configured_serve_host(
                command_json([*ts, "serve", "status", "--json"]), host, target
            )
            if configured_host is None:
                raise SetupError("The HTTPS proxy was not configured. Complete HTTPS setup and rerun this command.")
            host = configured_host
        endpoint = f"https://{host}"
        print(f"Private address: {endpoint}")
        print("\n4. Test from your desktop computer")
        print("With Tailscale connected on your desktop, run this in a desktop terminal:")
        print(f"\ncurl --noproxy '*' --connect-timeout 10 --max-time 20 {endpoint}/.well-known/t3/environment\n")
        print(f"It should report environmentId {descriptor['environmentId']}.")
        print("If it does not, check your desktop's Tailscale connection and your tailnet")
        print("access policy, then rerun setup.")
        input("Press Enter once it does, or Ctrl-C to stop: ")
        # Check again before minting a credential for a potentially restarted server.
        if t3_environment(args.port)["environmentId"] != descriptor["environmentId"]:
            raise SetupError("The T3 environment changed during setup. Rerun setup before pairing.")
        check_pairing_identity(args.base_dir, descriptor["environmentId"])
        link = pairing_url(binaries["t3"], args.base_dir, endpoint)
        print("\n5. Pair the T3 desktop app")
        print("In T3 Code, open Settings > Connections > Add environment and paste this")
        print("into the Host field. It fills in the host and the pairing code:")
        print(f"\n{link}\n")
        print("Keep the link private; it expires in five minutes. Rerun setup for a new one.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check local T3 and Tailscale configuration without changing it or generating tokens")
    parser.add_argument("--port", type=int, default=os.environ.get("NEURODESKTOP_T3_CODE_PORT", "3773"), help="T3 port, default NEURODESKTOP_T3_CODE_PORT or 3773")
    parser.add_argument("--base-dir", type=Path, default=Path(os.environ.get("NEURODESKTOP_T3_CODE_HOME", str(Path.home() / ".t3"))), help="T3 state directory, matching the Jupyter-managed server")
    parser.add_argument("--tailscale-hostname", default="neurodesktop", help="Stable tailnet device name, so the address survives a container restart; empty keeps the name Tailscale chooses")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/tailscale", help="Persistent Tailscale state directory; use one per instance")
    parser.add_argument("--socket", type=Path, default=Path(f"/tmp/tailscale-{os.geteuid()}/tailscaled.sock"), help="User-owned Tailscale socket; can reuse a manually started daemon")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.tailscale_hostname and not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", args.tailscale_hostname):
        parser.error("--tailscale-hostname must be a DNS label: letters, digits, and inner hyphens")
    for name in ("base_dir", "state_dir", "socket"):
        value = getattr(args, name).expanduser()
        if not value.is_absolute():
            parser.error(f"--{name.replace('_', '-')} must be an absolute path")
        setattr(args, name, value)
    try:
        setup(args)
    except (SetupError, OSError) as error:
        print(f"\nSetup stopped: {error}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("\nSetup timed out. Rerun it to continue; an existing Tailscale login is reused.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nSetup stopped. Rerun it to continue; a started Tailscale daemon is left running.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
