"""Exercise GPU diagnostics without a driver or display server."""

import json
import os
import subprocess

import pytest

from testlib import repo_path


CHECK = repo_path("config/lxde/neurodesktop-gpu-check")


@pytest.fixture
def probe(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "calls.jsonl"
    for name in ("vgl-eglinfo", "vglrun"):
        executable = binaries / name
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "name = pathlib.Path(sys.argv[0]).name\n"
            "with open(os.environ['PROBE_CALLS'], 'a') as log:\n"
            "    log.write(json.dumps([name, *sys.argv[1:]]) + '\\n')\n"
            "if name == 'vglrun':\n"
            "    print(os.environ['PROBE_OUTPUT'])\n"
            "sys.exit(int(os.environ.get('PROBE_EGL_STATUS' if name == 'vgl-eglinfo' else 'PROBE_GL_STATUS', '0')))\n"
        )
        executable.chmod(0o755)

    def run(arguments=(), **overrides):
        environment = {
            **os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
            "DISPLAY": ":12", "VGL_DISPLAY": "egl0",
            "PROBE_CALLS": str(calls),
            "PROBE_OUTPUT": "OpenGL vendor string: NVIDIA Corporation\nOpenGL renderer string: NVIDIA Test GPU",
            **overrides,
        }
        result = subprocess.run(
            ["bash", str(CHECK), *arguments], env=environment,
            capture_output=True, text=True, timeout=10,
        )
        recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return result, recorded

    return run


def test_help_needs_no_gpu_or_display(probe):
    result, calls = probe(["--help"], DISPLAY="")
    assert result.returncode == 0
    assert "EGL_DEVICE" in result.stdout
    assert calls == []


def test_missing_display_fails_before_rendering(probe):
    result, calls = probe(DISPLAY="")
    assert result.returncode == 1
    assert "desktop terminal" in result.stderr
    assert calls == []


@pytest.mark.parametrize("arguments,selected", [([], "egl2"), (["egl3"], "egl3")])
def test_nvidia_probe_uses_requested_device_and_proxy_transport(probe, arguments, selected):
    result, calls = probe(arguments, VGL_DISPLAY="egl2")
    assert result.returncode == 0
    assert calls == [
        ["vgl-eglinfo", selected],
        ["vglrun", "-d", selected, "-c", "proxy", "glxinfo", "-B"],
    ]
    assert "NVIDIA Test GPU" in result.stdout


def test_failed_egl_probe_never_attempts_application_rendering(probe):
    result, calls = probe(PROBE_EGL_STATUS="7")
    assert result.returncode == 7
    assert calls == [["vgl-eglinfo", "egl0"]]


def test_failed_gl_probe_cannot_pass_from_partial_vendor_output(probe):
    result, calls = probe(PROBE_GL_STATUS="9")
    assert result.returncode == 9
    assert len(calls) == 2
    assert "probe passed" not in result.stdout


def test_software_renderer_does_not_pass_nvidia_probe(probe):
    result, _ = probe(PROBE_OUTPUT="OpenGL vendor string: Mesa\nOpenGL renderer string: llvmpipe")
    assert result.returncode == 1
    assert "did not report the NVIDIA" in result.stderr


def test_image_pins_both_architecture_assets_and_graphics_capabilities():
    dockerfile = repo_path("Dockerfile").read_text()
    assert 'ARG VIRTUALGL_VERSION="3.1.5"' in dockerfile
    assert 'ARG VIRTUALGL_AMD64_SHA256="df3f7788ce41b182a47c0d298e5cd6d2d63579522cb41825970b7726e825485e"' in dockerfile
    assert 'ARG VIRTUALGL_ARM64_SHA256="9ac238e8a18c06d84ef65444a591a7a5bb4c4ce9e8cfdd92f8a2aea35edbb5d7"' in dockerfile
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics,display" in dockerfile
