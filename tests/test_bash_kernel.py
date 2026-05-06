"""Verify the bash Jupyter kernel is installed and usable."""
import json
import subprocess
from pathlib import Path

import pytest


KERNEL_SEARCH_ROOTS = (
    "/opt/conda/share/jupyter/kernels",
    "/usr/local/share/jupyter/kernels",
    "/usr/share/jupyter/kernels",
)


def _find_bash_kernelspec():
    for root in KERNEL_SEARCH_ROOTS:
        spec = Path(root) / "bash" / "kernel.json"
        if spec.is_file():
            return spec
    return None


def test_bash_kernelspec_installed():
    """A 'bash' kernelspec should be discoverable by Jupyter."""
    spec = _find_bash_kernelspec()
    assert spec is not None, (
        "bash kernelspec not found under any of: "
        + ", ".join(KERNEL_SEARCH_ROOTS)
    )
    data = json.loads(spec.read_text())
    assert data.get("language") == "bash", (
        f"bash kernelspec has unexpected language field: {data!r}"
    )


def test_bash_kernel_listed_by_jupyter():
    """`jupyter kernelspec list` should report the bash kernel."""
    result = subprocess.run(
        ["/opt/conda/bin/jupyter", "kernelspec", "list", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    kernels = payload.get("kernelspecs", {})
    assert "bash" in kernels, (
        f"bash kernel not in `jupyter kernelspec list`: {sorted(kernels)!r}"
    )


def test_bash_kernel_module_importable():
    """The bash_kernel Python package must be importable from the conda env."""
    result = subprocess.run(
        ["/opt/conda/bin/python", "-c", "import bash_kernel; print(bash_kernel.__name__)"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"bash_kernel is not importable: {result.stderr}"
    )
    assert "bash_kernel" in result.stdout


def test_bash_kernel_executes_via_nbconvert(tmp_path):
    """Execute a tiny bash notebook end-to-end via nbconvert to confirm the
    kernel actually runs (not just that the spec file exists).
    """
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": "echo hello-from-bash-kernel",
            }
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Bash",
                "language": "bash",
                "name": "bash",
            }
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    nb_path = tmp_path / "bash_smoke.ipynb"
    nb_path.write_text(json.dumps(nb))

    result = subprocess.run(
        [
            "/opt/conda/bin/jupyter",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            "--ExecutePreprocessor.kernel_name=bash",
            "--output",
            "bash_smoke.executed.ipynb",
            str(nb_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert result.returncode == 0, (
        f"nbconvert failed to execute the bash notebook: {result.stderr}"
    )

    executed = json.loads((tmp_path / "bash_smoke.executed.ipynb").read_text())
    outputs = executed["cells"][0].get("outputs", [])
    assert outputs, "Bash cell produced no outputs after execution"
    text = "".join(
        o.get("text", "") if isinstance(o.get("text"), str) else "".join(o.get("text", []))
        for o in outputs
    )
    assert "hello-from-bash-kernel" in text, (
        f"bash kernel did not echo the expected output. Got: {text!r}"
    )


def test_bash_kernel_negative_nonexistent_command(tmp_path):
    """Negative test: a bash cell that runs a non-existent command must
    cause `nbconvert --execute` to fail (non-zero exit) rather than
    silently succeed. Guards against the bash kernel swallowing errors.
    """
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": "set -euo pipefail\nfunny-name-tool --version",
            }
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Bash",
                "language": "bash",
                "name": "bash",
            }
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    nb_path = tmp_path / "bash_negative.ipynb"
    nb_path.write_text(json.dumps(nb))

    result = subprocess.run(
        [
            "/opt/conda/bin/jupyter",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            "--ExecutePreprocessor.kernel_name=bash",
            "--output",
            "bash_negative.executed.ipynb",
            str(nb_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert result.returncode != 0, (
        "nbconvert should have failed when the bash cell ran a non-existent "
        f"command, but it exited 0. stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not (tmp_path / "bash_negative.executed.ipynb").exists(), (
        "nbconvert produced an executed notebook despite the failing command"
    )
