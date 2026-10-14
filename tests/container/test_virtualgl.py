"""Installed VirtualGL tools, software desktop, and opt-in hardware probe."""

import os
import subprocess

import pytest

from native_desktop_driver import Desktop


def test_installed_virtualgl_and_diagnostics():
    result = subprocess.run(["vglrun", "--version"], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VirtualGL v3.1.5" in result.stdout + result.stderr
    result = subprocess.run(["neurodesktop-gpu-check", "--help"], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "EGL_DEVICE" in result.stdout


@pytest.mark.parametrize("command", [
    ["glxinfo", "-B"],
    ["vglrun", "-d", "egl0", "-c", "proxy", "glxinfo", "-B"],
])
def test_virtualgl_install_preserves_software_desktop(tmp_path, command):
    desktop = Desktop(tmp_path)
    try:
        result = subprocess.run(
            command,
            env={**os.environ, "DISPLAY": desktop.name, "LIBGL_ALWAYS_SOFTWARE": "1",
                 "__EGL_VENDOR_LIBRARY_FILENAMES": "/usr/share/glvnd/egl_vendor.d/50_mesa.json"},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "llvmpipe" in result.stdout.lower(), result.stdout
    finally:
        desktop.close()


@pytest.mark.skipif(os.environ.get("NEURODESKTOP_REQUIRE_GPU") != "1", reason="Explicit GPU acceptance profile only")
def test_nvidia_egl_renders_into_virtual_desktop(tmp_path):
    desktop = Desktop(tmp_path)
    try:
        result = subprocess.run(
            ["neurodesktop-gpu-check"], env={**os.environ, "DISPLAY": desktop.name},
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "NVIDIA rendering probe passed" in result.stdout
    finally:
        desktop.close()
