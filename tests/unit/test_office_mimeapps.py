"""Exercise the build-time association updater against real desktop files."""
import configparser
import subprocess
import sys

import pytest

from testlib import repo_path


SCRIPT = repo_path("config/lxde/update_office_mimeapps.py")
MIME = "application/vnd.oasis.opendocument.text"


def desktop(directory, version, mime=MIME):
    path = directory / f"libreoffice-{version}.desktop"
    path.write_text(f"[Desktop Entry]\nName=LibreOffice\nMimeType={mime};\n")
    return f"neurodesk-{path.name}"


def update(mimeapps, applications):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(mimeapps), str(applications)],
        capture_output=True, text=True, timeout=10,
    )


def test_newest_office_handler_wins_and_unrelated_settings_survive(tmp_path):
    applications = tmp_path / "applications"
    applications.mkdir()
    desktop(applications, "26_2_4")
    newest = desktop(applications, "26_10_0")
    mimeapps = tmp_path / "mimeapps.list"
    mimeapps.write_text(
        "[Default Applications]\ntext/plain=editor.desktop\n"
        f"{MIME}=xarchiver.desktop\n"
        "[Added Associations]\ntext/plain=editor.desktop;other.desktop;\n"
        "[Removed Associations]\ntext/plain=old.desktop;\n"
    )
    result = update(mimeapps, applications)
    assert result.returncode == 0, result.stderr
    parsed = configparser.ConfigParser(interpolation=None)
    parsed.read(mimeapps)
    assert parsed["Default Applications"][MIME] == newest
    assert parsed["Added Associations"][MIME] == newest + ";"
    assert parsed["Removed Associations"][MIME] == "xarchiver.desktop;"
    assert parsed["Default Applications"]["text/plain"] == "editor.desktop"
    assert parsed["Added Associations"]["text/plain"] == "editor.desktop;other.desktop;"
    assert parsed["Removed Associations"]["text/plain"] == "old.desktop;"
    first = mimeapps.read_bytes()
    assert update(mimeapps, applications).returncode == 0
    assert mimeapps.read_bytes() == first


def test_each_mime_type_uses_its_newest_available_handler(tmp_path):
    older = desktop(tmp_path, "26_2_4", "text/csv;" + MIME)
    newer = desktop(tmp_path, "26_10_0", MIME)
    mimeapps = tmp_path / "mimeapps.list"
    assert update(mimeapps, tmp_path).returncode == 0
    parsed = configparser.ConfigParser()
    parsed.read(mimeapps)
    assert dict(parsed["Default Applications"]) == {"text/csv": older, MIME: newer}


@pytest.mark.parametrize("empty_entry", [False, True])
def test_missing_mime_declarations_fail_without_modifying_defaults(tmp_path, empty_entry):
    if empty_entry:
        desktop(tmp_path, "26_10_0", "")
    mimeapps = tmp_path / "mimeapps.list"
    original = b"[Default Applications]\ntext/plain=editor.desktop\n"
    mimeapps.write_bytes(original)
    result = update(mimeapps, tmp_path)
    assert result.returncode != 0
    assert "declares MIME types" in result.stderr
    assert mimeapps.read_bytes() == original
