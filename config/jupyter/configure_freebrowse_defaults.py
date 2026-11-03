"""Keep FreeBrowse optional for volumes and default for NiiVue documents."""

import sys
from pathlib import Path


def configure_defaults(path: Path) -> None:
    source = path.read_text()
    original = 'defaultFor: ["nifti", "nifti-gz", "nvd"],'
    replacement = 'defaultFor: ["nvd"],'
    if source.count(original) != 1:
        raise ValueError("FreeBrowse default factory registration changed")
    path.write_text(source.replace(original, replacement))


if __name__ == "__main__":
    configure_defaults(Path(sys.argv[1]))
