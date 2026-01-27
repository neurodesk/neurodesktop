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
from pathlib import Path
from typing import Dict, Any

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

    # Determine file extension from URL
    ext = Path(url).suffix or ".svg"
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
        # Use Unix socket - path is deterministic from app name (no port conflicts!)
        socket_path = f"/tmp/neurodesk_webapp_{name}.sock"

        # Main webapp entry
        # Note: icon_path only works when category is "Notebook" or "Console" (JupyterLab limitation)
        category = config.get('category', 'Console')
        icon_config = config.get('icon', '/opt/neurodesk_brain_icon.svg')

        # If icon is a URL, download it locally (JupyterLab needs local file paths)
        if icon_config.startswith(('http://', 'https://')):
            icon_path = download_icon(icon_config, name)
        else:
            icon_path = icon_config

        startup_timeout = config.get('startup_timeout', 120)
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


def generate_config(webapps_json_path: Path, template_path: Path, output_path: Path):
    """
    Generate jupyter_notebook_config.py from template and webapps.json.

    Args:
        webapps_json_path: Path to webapps.json
        template_path: Path to jupyter_notebook_config.py.template
        output_path: Path to write generated config
    """
    # Load webapps.json
    print(f"Loading webapps from: {webapps_json_path}")
    with open(webapps_json_path, 'r') as f:
        data = json.load(f)

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
        print(f"  - {name}: {config.get('title')} (socket: /tmp/neurodesk_webapp_{name}.sock)")


def main():
    if len(sys.argv) != 4:
        print("Usage: generate_jupyter_config.py <webapps.json> <template.py> <output.py>")
        print()
        print("Arguments:")
        print("  webapps.json  Path to webapp configurations JSON file")
        print("  template.py   Path to jupyter_notebook_config.py.template")
        print("  output.py     Path to write generated jupyter_notebook_config.py")
        sys.exit(1)

    webapps_json_path = Path(sys.argv[1])
    template_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])

    if not webapps_json_path.exists():
        print(f"Error: webapps.json not found: {webapps_json_path}")
        sys.exit(1)

    if not template_path.exists():
        print(f"Error: template not found: {template_path}")
        sys.exit(1)

    generate_config(webapps_json_path, template_path, output_path)


if __name__ == "__main__":
    main()
