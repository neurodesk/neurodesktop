"""Office documents must open in the Neurodesk LibreOffice container apps.

Double-clicking a document in the desktop resolves its MIME type through
GIO, so these tests assert the association layer that pcmanfm uses:
config/lxde/update_office_mimeapps.py registers defaults at image build
time from the MimeType declarations that neurocommand writes into its
generated .desktop files. Without those, .odt/.docx used to open in
xarchiver because ODF/OOXML files are zip containers.
"""
import configparser
import subprocess
from pathlib import Path

import pytest

DEFAULTS_CONFIG = "/opt/jovyan_defaults/.config"
NEURODESK_APPDIR = Path("/usr/share/applications/neurodesk")

OFFICE_MIMETYPES = {
    "application/vnd.oasis.opendocument.text": "libreofficewritergui",
    "application/msword": "libreofficewritergui",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "libreofficewritergui",
    "application/vnd.oasis.opendocument.spreadsheet": "libreofficecalcgui",
    "application/vnd.ms-excel": "libreofficecalcgui",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "libreofficecalcgui",
    "application/vnd.oasis.opendocument.presentation": "libreofficeimpressgui",
    "application/vnd.ms-powerpoint": "libreofficeimpressgui",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "libreofficeimpressgui",
}


def run_cmd(cmd):
    process = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "XDG_CONFIG_HOME": DEFAULTS_CONFIG,
            "XDG_DATA_DIRS": "/usr/local/share:/usr/share",
        },
    )
    return process.returncode, process.stdout.strip()


def read_desktop_entry(desktop_id):
    assert desktop_id.startswith("neurodesk-")
    path = NEURODESK_APPDIR / desktop_id.removeprefix("neurodesk-")
    assert path.is_file(), f"default handler {desktop_id} does not exist"
    entry = configparser.ConfigParser(interpolation=None, strict=False)
    entry.optionxform = str
    entry.read(path)
    return entry["Desktop Entry"]


@pytest.mark.parametrize("mimetype,component", OFFICE_MIMETYPES.items())
def test_office_mimetype_defaults_to_libreoffice(mimetype, component):
    code, default = run_cmd(f"xdg-mime query default {mimetype}")
    assert code == 0
    assert default.startswith(f"neurodesk-{component}-libreoffice-"), (
        f"{mimetype} resolves to {default!r} instead of a Neurodesk "
        f"{component} entry"
    )

    # The handler must exist, forward the clicked file (%F) and claim the type
    entry = read_desktop_entry(default)
    assert "%F" in entry["Exec"]
    assert f"{mimetype};" in entry["MimeType"]

    # The wrapper script must forward the document path even with spaces
    wrapper = entry["Exec"].replace(" %F", "").removeprefix("/bin/bash ")
    assert '"$@"' in Path(wrapper).read_text()


@pytest.mark.parametrize(
    "mimetype",
    [
        "application/vnd.oasis.opendocument.text",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
)
def test_xarchiver_no_longer_claims_office_documents(mimetype):
    code, output = run_cmd(f"gio mime {mimetype}")
    assert code == 0
    assert "xarchiver" not in output, (
        f"xarchiver is still offered for {mimetype}:\n{output}"
    )
