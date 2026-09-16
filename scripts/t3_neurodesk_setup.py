#!/usr/bin/env python3
"""Interactive, unprivileged T3/Tailscale setup. No third-party Python dependencies."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request


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
    if not re.fullmatch(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+\.ts\.net", hostname):
        raise SetupError("No complete device DNS name is available. Enable MagicDNS in your tailnet.")
    return hostname


def serve_matches(config, hostname, target):
    """Refuse to replace an existing handler or reuse a public Funnel."""
    if any(config.get("AllowFunnel", {}).values()):
        raise SetupError("Funnel is enabled. Disable public access before using this private setup.")
    listener = config.get("TCP", {}).get("443")
    web = config.get("Web", {}).get(f"{hostname}:443")
    if listener is None and web is None:
        return False
    if listener != {"HTTPS": True} or web != {"Handlers": {"/": {"Proxy": target}}}:
        raise SetupError(
            "Tailscale port 443 already has a different configuration. "
            "Setup has left it unchanged; review it with tailscale serve status."
        )
    return True


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
    print(f"T3 is responding. Environment ID: {descriptor['environmentId']}")
    ts = [binaries["tailscale"], f"--socket={args.socket}"]
    if args.check:
        if not args.socket.exists():
            raise SetupError("No Tailscale socket found. Run t3_neurodesk_setup interactively to start it.")
        host = device_host(command_json([*ts, "status", "--json"]))
        if not serve_matches(command_json([*ts, "serve", "status", "--json"]), host, f"http://127.0.0.1:{args.port}"):
            raise SetupError("Tailscale is connected but the T3 HTTPS proxy is not configured.")
        print(f"Private endpoint configured: https://{host}")
        print("This local check does not verify access from your desktop.")
        return

    print("\nThis connects your Neurodesktop to your Tailscale network.")
    print("You need Tailscale on your desktop, signed into the same tailnet.")
    print("A daemon started by setup keeps running after this terminal closes.")
    print("If reusing a manually started daemon, keep its original terminal running. Rerun setup after a container restart.")
    input("Press Enter to continue, or Ctrl-C to stop: ")
    private_directory(args.socket.parent)
    with (args.socket.parent / "setup.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError("Another setup is running. Finish it before starting this one.") from None
        print("\n2. Connect Tailscale")
        status = ensure_daemon(binaries["tailscaled"], ts, args.socket, args.state_dir)
        if status.get("BackendState") != "Running":
            print("Open the login link below in your browser and complete any device approval.")
            print("Keep the login link private. You have ten minutes to complete this step.")
            interactive([*ts, "up", "--accept-dns=false"])
            status = command_json([*ts, "status", "--json"])
        host = device_host(status)
        target = f"http://127.0.0.1:{args.port}"
        print("\n3. Configure private HTTPS access")
        if not serve_matches(command_json([*ts, "serve", "status", "--json"]), host, target):
            print("If Tailscale asks you to enable HTTPS, follow its link. Then rerun setup if requested.")
            interactive([*ts, "serve", "--bg", "--https=443", target])
            if not serve_matches(command_json([*ts, "serve", "status", "--json"]), host, target):
                raise SetupError("The HTTPS proxy was not configured. Complete HTTPS setup and rerun this command.")
        endpoint = f"https://{host}"
        print("\n4. Test from your desktop computer")
        print("Connect Tailscale on your desktop, then run this in a desktop terminal:")
        print(f"\ncurl --noproxy '*' --connect-timeout 10 --max-time 20 {endpoint}/.well-known/t3/environment\n")
        print(f"The JSON should contain environmentId: {descriptor['environmentId']}")
        if input("Did your desktop return that environment ID? [y/N]: ").strip().lower() not in {"y", "yes"}:
            raise SetupError(
                "Pairing paused. Check your desktop's Tailscale connection, tailnet access policy, "
                "and the full device hostname above. Rerun setup when desktop access works."
            )
        # Check again before minting a credential for a potentially restarted server.
        if t3_environment(args.port)["environmentId"] != descriptor["environmentId"]:
            raise SetupError("The T3 environment changed during setup. Rerun setup before pairing.")
        check_pairing_identity(args.base_dir, descriptor["environmentId"])
        print("\n5. Pair the T3 desktop app")
        print("Open Settings > Connections > Add environment.")
        print(f"Host: {endpoint}")
        print("Use the Token printed below as the pairing code. Ignore the generated container-IP pairing URL.")
        print("Keep the token private; it expires in five minutes.", flush=True)
        interactive([binaries["t3"], "pair", "--base-dir", str(args.base_dir)], timeout=30)
        print(f"\nDesktop host: {endpoint}")
        print("In this remote environment's provider settings, use these Binary paths:")
        print("  Codex:  /opt/neurodesktop/t3-provider-bin/codex")
        print("  Claude: /opt/neurodesktop/t3-provider-bin/claude")
        print("Authenticate providers in Neurodesktop if prompted, then refresh their status.")
        print("Tailscale is connected. Your desktop connection completes when you submit the pairing code.")
        print("To stop this device's Tailscale connection:")
        print(shlex.join([*ts, "down"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check local T3 and Tailscale configuration without changing it or generating tokens")
    parser.add_argument("--port", type=int, default=os.environ.get("NEURODESKTOP_T3_CODE_PORT", "3773"), help="T3 port, default NEURODESKTOP_T3_CODE_PORT or 3773")
    parser.add_argument("--base-dir", type=Path, default=Path(os.environ.get("NEURODESKTOP_T3_CODE_HOME", str(Path.home() / ".t3"))), help="T3 state directory, matching the Jupyter-managed server")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/tailscale", help="Persistent Tailscale state directory; use one per instance")
    parser.add_argument("--socket", type=Path, default=Path(f"/tmp/tailscale-{os.geteuid()}/tailscaled.sock"), help="User-owned Tailscale socket; can reuse a manually started daemon")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
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
