"""Build-time workaround contract for the jupyter-server-mcp FastMCP banner.

jupyter-server-mcp 0.2.1 starts FastMCP through its own embedded HTTP runner
and calls log_server_banner() unconditionally, so FASTMCP_SHOW_SERVER_BANNER
is ignored and a multi-line ASCII banner lands in the Jupyter server log on
every boot. The patcher gates the call on the FastMCP setting the image
already exports.
"""

import pytest

from testlib import load_source_module, repo_path


SERVER_SOURCE = '''import uvicorn
from fastmcp import settings as fastmcp_settings
from fastmcp.utilities.cli import log_server_banner


class MCPServer:
    async def _run_http_async_without_signals(self, host, port):
        """Run FastMCP over HTTP without taking over process signal handlers."""
        transport = "http"
        app = self.mcp.http_app(transport=transport)

        log_server_banner(server=self.mcp)

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=fastmcp_settings.log_level.lower(),
        )
'''


def load_patcher_module():
    return load_source_module(
        "jupyter_server_mcp_patch",
        "/opt/neurodesktop/patch_jupyter_server_mcp.py",
        "config/jupyter/patch_jupyter_server_mcp.py",
    )


def write_upstream_fixture(package_dir):
    (package_dir / "mcp_server.py").write_text(SERVER_SOURCE, encoding="utf-8")


def test_patch_gates_banner_on_setting_and_is_idempotent(tmp_path):
    patcher = load_patcher_module()
    write_upstream_fixture(tmp_path)

    assert patcher.patch_package(tmp_path)

    text = (tmp_path / "mcp_server.py").read_text(encoding="utf-8")
    assert patcher.MARKER in text
    assert (
        "        if fastmcp_settings.show_server_banner:\n"
        "            log_server_banner(server=self.mcp)\n"
    ) in text
    # No unconditional call remains.
    assert "\n        log_server_banner(server=self.mcp)\n" not in text
    # The patched module must still be valid Python.
    compile(text, "mcp_server.py", "exec")

    assert not patcher.patch_package(tmp_path)


def test_patch_refuses_anchor_drift(tmp_path):
    """A jupyter-server-mcp upgrade that reshapes the runner must fail the
    image build instead of silently dropping or misapplying the workaround."""
    patcher = load_patcher_module()
    (tmp_path / "mcp_server.py").write_text(
        "from fastmcp import settings as fastmcp_settings\nupstream changed\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="banner anchor"):
        patcher.patch_package(tmp_path)


def test_patch_refuses_when_settings_import_is_gone(tmp_path):
    """The replacement references fastmcp_settings; without the import the
    patched module would crash at banner time."""
    patcher = load_patcher_module()
    (tmp_path / "mcp_server.py").write_text(
        "        log_server_banner(server=self.mcp)\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="settings"):
        patcher.patch_package(tmp_path)


def test_image_disables_the_banner_and_applies_workaround_after_pin():
    dockerfile = repo_path("Dockerfile").read_text(encoding="utf-8")

    # The setting the patched call honors must be exported image-wide.
    assert "ENV FASTMCP_SHOW_SERVER_BANNER=0" in dockerfile

    package_pin = dockerfile.index("jupyter-server-mcp==0.2.1")
    patch_install = dockerfile.index("/opt/neurodesktop/patch_jupyter_server_mcp.py")
    patch_run = dockerfile.index(
        "/opt/conda/bin/python /opt/neurodesktop/patch_jupyter_server_mcp.py"
    )
    assert package_pin < patch_install < patch_run
