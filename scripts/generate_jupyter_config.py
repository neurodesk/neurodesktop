#!/usr/bin/env python3
"""
Generate jupyter_notebook_config.py from webapps.json and a template.

This script reads webapp configurations from webapps.json and generates
the ServerProxy.servers entries for JupyterLab integration.
"""

import json
import sys
import urllib.request
import urllib.error
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any
from urllib.parse import urlparse

# Directory to store downloaded webapp icons
ICONS_DIR = Path("/opt/neurodesk/icons")


def download_icon(url: str, name: str) -> str:
    """
    Download an icon from a URL and save it locally.

    Args:
        url: URL to download the icon from
        name: Name of the webapp (used for the local filename)

    Returns:
        Local path to the downloaded icon, or default icon path on failure
    """
    default_icon = "/opt/neurodesk_brain_icon.svg"

    # Determine file extension from the URL path, ignoring query strings.
    ext = Path(urlparse(url).path).suffix or ".svg"
    local_path = ICONS_DIR / f"{name}{ext}"

    try:
        ICONS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"  Downloading icon for {name}: {url}")
        urllib.request.urlretrieve(url, local_path)
        print(f"    Saved to: {local_path}")
        return str(local_path)
    except Exception as e:
        print(f"  Warning: Failed to download icon for {name}: {e}")
        return default_icon


def generate_server_proxy_entries(webapps: Dict[str, Any]) -> str:
    """
    Generate Python code for ServerProxy.servers webapp entries.

    Args:
        webapps: Dict of webapp configurations from webapps.json

    Returns:
        Python code string for webapp server entries
    """
    entries = []

    for name, config in sorted(webapps.items()):
        category = config.get('category', 'Webapps')
        icon_config = config.get('icon', '/opt/neurodesk_brain_icon.svg')

        # If icon is a URL, download it locally (JupyterLab needs local file paths)
        if icon_config.startswith(('http://', 'https://')):
            icon_path = download_icon(icon_config, name)
        else:
            icon_path = icon_config

        startup_timeout = config.get('startup_timeout', 120)
        direct_url = config.get('direct_url')

        if direct_url:
            if not direct_url.startswith(('http://', 'https://')):
                raise ValueError(f"direct_url for {name} must be an HTTP(S) URL")

            entry = f"""  '{name}': {{
    'command': ['python3', '/opt/neurodesktop/external_webapp_redirect.py', '--url', '{direct_url}', '--port', '{{port}}'],
    'timeout': 10,
    'absolute_url': True,
    'new_browser_tab': True,
    'launcher_entry': {{
      'path_info': '{name}',
      'title': '{config.get('title', name)}',
      'icon_path': '{icon_path}',
      'category': '{category}',
      'url': '{direct_url}'
    }}
  }}"""
            entries.append(entry)
            continue

        # Use Unix socket - path is deterministic from app name (no port conflicts!)
        socket_path = f"/tmp/neurodesk_webapp_{name}.sock"

        # Main webapp entry. The custom Neurodesk launcher extension reads
        # icon_path values through the server-proxy icon endpoint.
        entry = f"""  '{name}': {{
    'command': ['/opt/neurodesktop/webapp_launcher.sh', '{name}'],
    'unix_socket': '{socket_path}',
    'timeout': {startup_timeout},
    'absolute_url': True,
    'new_browser_tab': True,
    'launcher_entry': {{
      'path_info': '{name}',
      'title': '{config.get('title', name)}',
      'icon_path': '{icon_path}',
      'category': '{category}'
    }}
  }}"""
        entries.append(entry)

        # Additional proxy entries - only register separately if they're NOT under the app's path
        # Routes under the app path (e.g., ezbids/api) are handled by the main entry
        for proxy in config.get("additional_proxies", []):
            proxy_path = proxy['path']
            # Skip if the proxy path is under the main app path (will be handled by main entry)
            if proxy_path.startswith(f"{name}/"):
                continue
            proxy_entry = f"""  '{proxy_path}': {{
    'command': ['/opt/neurodesktop/webapp_launcher.sh', '{name}'],
    'unix_socket': '{socket_path}',
    'timeout': {startup_timeout},
    'absolute_url': True,
    'launcher_entry': {{
      'enabled': False
    }}
  }}"""
            entries.append(proxy_entry)

    return ",\n".join(entries)


def merge_webapp_configs(base_config: Dict[str, Any], overlay_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge a local webapp overlay into the fetched webapps config.

    Existing webapp entries are updated key-by-key. New overlay entries are
    appended. This lets Neurodesktop override launcher-only behavior without
    replacing the neurocommand-owned container app definitions.
    """
    merged = deepcopy(base_config)
    merged_webapps = merged.setdefault("webapps", {})

    for name, overlay_webapp in overlay_config.get("webapps", {}).items():
        if not isinstance(overlay_webapp, dict):
            raise ValueError(f"Overlay webapp {name} must be an object")

        existing_webapp = merged_webapps.get(name, {})
        if existing_webapp and not isinstance(existing_webapp, dict):
            raise ValueError(f"Base webapp {name} must be an object")

        updated_webapp = dict(existing_webapp)
        updated_webapp.update(overlay_webapp)
        merged_webapps[name] = updated_webapp

    return merged


def load_webapps_config(webapps_json_path: Path, overlay_paths: list[Path] | None = None) -> Dict[str, Any]:
    print(f"Loading webapps from: {webapps_json_path}")
    with open(webapps_json_path, 'r') as f:
        data = json.load(f)

    for overlay_path in overlay_paths or []:
        print(f"Applying webapp overlay: {overlay_path}")
        with open(overlay_path, 'r') as f:
            overlay_data = json.load(f)
        data = merge_webapp_configs(data, overlay_data)

    return data


def generate_config(
    webapps_json_path: Path,
    template_path: Path,
    output_path: Path,
    overlay_paths: list[Path] | None = None,
):
    """
    Generate jupyter_notebook_config.py from template and webapps.json.

    Args:
        webapps_json_path: Path to webapps.json
        template_path: Path to jupyter_notebook_config.py.template
        output_path: Path to write generated config
        overlay_paths: Optional local webapp overlay JSON files
    """
    data = load_webapps_config(webapps_json_path, overlay_paths)

    webapps = data.get("webapps", {})
    print(f"  Found {len(webapps)} webapp(s)")

    # Load template
    print(f"Loading template from: {template_path}")
    with open(template_path, 'r') as f:
        template = f.read()

    # Generate webapp entries
    if webapps:
        webapp_entries = generate_server_proxy_entries(webapps)
        # Add comma before webapp entries since they follow the neurodesktop entry
        replacement = ",\n" + webapp_entries
    else:
        replacement = ""

    # Replace placeholder in template
    output = template.replace("# {{WEBAPP_SERVERS}}", replacement)

    # Write output
    print(f"Writing config to: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(output)

    print("Done!")
    for name, config in webapps.items():
        if config.get('direct_url'):
            print(f"  - {name}: {config.get('title')} (direct: {config.get('direct_url')})")
        else:
            print(f"  - {name}: {config.get('title')} (socket: /tmp/neurodesk_webapp_{name}.sock)")


def main():
    if len(sys.argv) < 4:
        print("Usage: generate_jupyter_config.py <webapps.json> <template.py> <output.py> [overlay.json ...]")
        print()
        print("Arguments:")
        print("  webapps.json  Path to webapp configurations JSON file")
        print("  template.py   Path to jupyter_notebook_config.py.template")
        print("  output.py     Path to write generated jupyter_notebook_config.py")
        print("  overlay.json  Optional local webapp overlay JSON file(s)")
        sys.exit(1)

    webapps_json_path = Path(sys.argv[1])
    template_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    overlay_paths = [Path(arg) for arg in sys.argv[4:]]

    if not webapps_json_path.exists():
        print(f"Error: webapps.json not found: {webapps_json_path}")
        sys.exit(1)

    if not template_path.exists():
        print(f"Error: template not found: {template_path}")
        sys.exit(1)

    for overlay_path in overlay_paths:
        if not overlay_path.exists():
            print(f"Error: overlay not found: {overlay_path}")
            sys.exit(1)

    generate_config(webapps_json_path, template_path, output_path, overlay_paths)


if __name__ == "__main__":
    main()
