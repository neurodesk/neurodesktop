"""Smoke tests for LibreOffice installation."""

import os
import subprocess

import pytest


def _run(cmd):
    return subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_libreoffice_commands_available():
    """Calc and Writer must be installed for spreadsheet/document support."""
    for binary in ("localc", "lowriter", "soffice"):
        result = _run(f"command -v {binary}")
        assert result.returncode == 0, f"{binary} is not available in PATH"


def test_libreoffice_file_format_conversion(tmp_path):
    """LibreOffice must be able to create and read a simple spreadsheet."""
    if os.geteuid() != 0:
        pytest.skip("soffice headless conversion requires a writable home dir")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("a,b\n1,2\n")

    result = _run(
        f"soffice --headless --convert-to xlsx --outdir {out_dir} {csv_file}"
    )
    assert result.returncode == 0, (
        f"soffice conversion failed: {result.stdout}"
    )
    assert (out_dir / "data.xlsx").exists(), "expected converted XLSX file"
