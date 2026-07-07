#!/usr/bin/env python3
"""Register Neurodesk LibreOffice menu entries as office document handlers.

Runs at image build time after neurocommand has generated its application
menu. Reads the MimeType declarations from the generated LibreOffice
.desktop files (so the MIME type lists stay in sync with neurocommand),
points each declared type at the newest available LibreOffice entry in the
default-user mimeapps.list, and removes xarchiver's claim on those types.
Without the removal, double-clicking .odt/.docx files opens them in the
archive manager because ODF/OOXML documents are zip containers.

Usage: update_office_mimeapps.py [mimeapps.list] [applications dir]

Exits non-zero when no LibreOffice entry declares MIME types, which fails
the image build: that means the neurocommand version in the image predates
MimeType support in its menu generator.
"""
import configparser
import re
import sys
from pathlib import Path


def version_key(path):
    """Natural sort so libreoffice-26_10_0 sorts after libreoffice-26_2_4."""
    return [int(part) if part.isdigit() else part
            for part in re.split(r"(\d+)", path.name)]


def read_keyfile(path):
    keyfile = configparser.ConfigParser(interpolation=None, strict=False)
    keyfile.optionxform = str
    keyfile.read(path)
    return keyfile


def main():
    mimeapps = Path(sys.argv[1] if len(sys.argv) > 1
                    else "/opt/jovyan_defaults/.config/mimeapps.list")
    appdir = Path(sys.argv[2] if len(sys.argv) > 2
                  else "/usr/share/applications/neurodesk")

    defaults = {}
    for desktop in sorted(appdir.glob("libreoffice*.desktop"), key=version_key):
        entry = read_keyfile(desktop)["Desktop Entry"]
        for mime in filter(None, entry.get("MimeType", "").split(";")):
            # sorted oldest to newest, so the newest version wins
            defaults[mime] = f"neurodesk-{desktop.name}"

    if not defaults:
        sys.exit(f"No LibreOffice desktop entry in {appdir} declares MIME "
                 "types. The neurocommand version in this image is too old "
                 "for office file associations.")

    merged = read_keyfile(mimeapps)
    for section in ("Default Applications", "Added Associations",
                    "Removed Associations"):
        if not merged.has_section(section):
            merged.add_section(section)
    for mime, desktop_id in sorted(defaults.items()):
        merged["Default Applications"][mime] = desktop_id
        merged["Added Associations"][mime] = f"{desktop_id};"
        merged["Removed Associations"][mime] = "xarchiver.desktop;"

    with open(mimeapps, "w") as fh:
        merged.write(fh, space_around_delimiters=False)
    print(f"Registered {len(defaults)} office MIME defaults in {mimeapps}")


if __name__ == "__main__":
    main()
