import json
import shutil
import subprocess


def test_nbconvert_can_export_pdf(tmp_path):
    """Verify notebook PDF export works with the bundled LaTeX toolchain."""
    notebook_path = tmp_path / "example.ipynb"
    pdf_path = tmp_path / "example.pdf"

    notebook_payload = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Neurodesktop PDF export\n", "\n", "This is a smoke test.\n"],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["print('hello from neurodesktop')\n"],
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "version": "3",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    notebook_path.write_text(json.dumps(notebook_payload), encoding="utf-8")

    assert shutil.which("xelatex"), "xelatex must be installed for nbconvert PDF export"

    process = subprocess.run(
        [
            "jupyter",
            "nbconvert",
            "--to",
            "pdf",
            str(notebook_path),
            "--output",
            pdf_path.stem,
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    )

    assert process.returncode == 0, process.stdout
    assert pdf_path.exists(), process.stdout
    assert pdf_path.stat().st_size > 0, "Generated PDF is empty"
