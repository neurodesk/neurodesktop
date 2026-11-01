"""Installed-image acceptance for neurodesk-kernel against a real CVMFS container."""
import json
import os
import subprocess
import time

import pytest

MODULE = "fsl/6.0.7.22"
KERNEL = "neurodesk-fsl-6.0.7.22"


@pytest.fixture
def kernel_data_dir(tmp_path, monkeypatch):
    if os.environ.get("CVMFS_DISABLE", "false").lower() in {"true", "1"}:
        assert os.environ.get("NEURODESKTOP_REQUIRE_APPLICATIONS") != "1", (
            "Required container-kernel acceptance cannot run with CVMFS disabled")
        pytest.skip("CVMFS is explicitly disabled")
    data_dir = tmp_path / "jupyter-data"
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(data_dir))
    return data_dir


def neurodesk_kernel(*args):
    return subprocess.run(
        ["/usr/local/bin/neurodesk-kernel", *args],
        capture_output=True, text=True, timeout=900, check=False)


def test_container_kernel_runs_interrupts_and_is_removed(kernel_data_dir):
    import jupyter_client.manager

    added = neurodesk_kernel("add", MODULE)
    assert added.returncode == 0, added.stdout + added.stderr
    spec = json.loads((kernel_data_dir / "kernels" / KERNEL / "kernel.json").read_text())
    assert spec["argv"][0] == "/opt/neurodesktop/kernel_wrapper.sh"
    assert spec["argv"][1].startswith("/cvmfs/neurodesk.ardc.edu.au/containers/fsl_6.0.7.22_")

    manager, client = jupyter_client.manager.start_new_kernel(
        kernel_name=KERNEL, startup_timeout=300)
    try:
        output = []
        reply = client.execute_interactive(
            "import os, sys; print(sys.executable, os.environ.get('FSLDIR'))",
            output_hook=lambda msg: output.append(msg["content"].get("text", "")),
            timeout=120)
        assert reply["content"]["status"] == "ok"
        executable, fsldir = "".join(output).split()
        assert executable.startswith("/opt/fsl-6.0.7.22/"), executable
        assert fsldir == "/opt/fsl-6.0.7.22"

        # Jupyter interrupts the kernel's process group; the signal has to
        # cross the wrapper and Apptainer to reach the container's Python.
        client.execute("import time; time.sleep(120)")
        time.sleep(3)
        manager.interrupt_kernel()
        interrupted = client.get_shell_msg(timeout=60)
        assert interrupted["content"].get("ename") == "KeyboardInterrupt"
        assert client.execute_interactive("1", timeout=60)["content"]["status"] == "ok"
    finally:
        client.stop_channels()
        manager.shutdown_kernel(now=True)

    listed = neurodesk_kernel("list")
    assert f"{KERNEL}\t{MODULE}\t" in listed.stdout
    removed = neurodesk_kernel("remove", MODULE)
    assert removed.returncode == 0, removed.stderr
    assert not (kernel_data_dir / "kernels" / KERNEL).exists()


def test_container_python_without_ipykernel_is_refused(kernel_data_dir):
    refused = neurodesk_kernel("add", "datalad/1.7.2")
    assert refused.returncode == 1
    assert "ipykernel is not installed in the container" in refused.stderr
    assert not (kernel_data_dir / "kernels").exists()
