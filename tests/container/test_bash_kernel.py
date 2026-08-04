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
    """A bash cell that runs a non-existent command must surface the error
    in the executed notebook — either as a non-zero nbconvert exit, or as
    the failing command's message in a stream output. Guards against the
    bash kernel silently dropping the error entirely.

    bash_kernel does not reliably propagate cell exit codes to nbclient as
    `status: error`, so a strict "nbconvert returncode != 0" assertion is
    too tight. The user-visible contract is that the error appears in the
    notebook output; that's what we check.
    """
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": "funny-name-tool --version",
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
    out_name = "bash_negative.executed.ipynb"
    out_path = tmp_path / out_name

    result = subprocess.run(
        [
            "/opt/conda/bin/jupyter",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            "--ExecutePreprocessor.kernel_name=bash",
            "--output",
            out_name,
            str(nb_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )

    if result.returncode != 0:
        return

    assert out_path.exists(), (
        "nbconvert exited 0 but produced no executed notebook. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    executed = json.loads(out_path.read_text())
    outputs = executed["cells"][0].get("outputs", [])
    text_chunks = []
    for o in outputs:
        t = o.get("text", "")
        text_chunks.append(t if isinstance(t, str) else "".join(t))
        # Cells that error via `status: error` carry an "ename"/"evalue".
        text_chunks.append(o.get("evalue", ""))
    combined = "".join(text_chunks).lower()
    assert (
        "not found" in combined
        or "no such file" in combined
        or "funny-name-tool" in combined
    ), (
        "bash kernel silently swallowed the failing command — neither a "
        "non-zero nbconvert exit nor any error text in the executed cell. "
        f"stderr={result.stderr!r}; cell outputs={outputs!r}"
    )
