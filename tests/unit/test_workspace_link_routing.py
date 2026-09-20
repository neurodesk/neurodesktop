"""Execute the routing functions and click handler from the shipped TypeScript."""
from pathlib import Path
import subprocess

from testlib import repo_path


def test_workspace_link_routing_behavior():
    result = subprocess.run(
        ["node", "--experimental-vm-modules", str(Path(__file__).with_name("workspace_link_cases.mjs")),
         str(repo_path("extensions/neurodesk-launcher/src/workspaceLinks.ts"))],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_plugin_is_registered_alongside_the_launcher():
    index = repo_path("extensions/neurodesk-launcher/src/index.ts").read_text()
    assert "import workspaceLinksPlugin from './workspaceLinks';" in index
    assert "export default [plugin, workspaceLinksPlugin, astraViewerPlugin, t3CodePlugin];" in index
