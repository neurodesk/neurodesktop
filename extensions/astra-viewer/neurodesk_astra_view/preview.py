"""Confined, size-capped artifact previews for the ASTRA inspector."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any


DEFAULT_MAX_BYTES = 5 * 1024 * 1024
INLINE_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


class PreviewError(ValueError):
    """Raised when an artifact cannot be previewed within the safe boundary."""


def _safe_file(path: str | Path, project_root: str | Path) -> tuple[Path, Path]:
    try:
        root = Path(project_root).resolve(strict=True)
        resolved = Path(path).resolve(strict=True)
    except OSError as error:
        raise PreviewError(f"artifact is unavailable: {error}") from error
    if not resolved.is_relative_to(root):
        raise PreviewError(f"artifact escapes the ASTRA project root: {path}")
    if not resolved.is_file():
        raise PreviewError(f"artifact is not a regular file: {path}")
    return resolved, root


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _base(path: Path, root: Path) -> dict[str, Any]:
    size = path.stat().st_size
    return {
        "kind": "link",
        "path": str(path.relative_to(root)),
        "size": size,
        "sha256": _sha256(path),
        "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    }


def _metric(path: Path, units: str | None) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise PreviewError(f"metric cannot be read as text: {error}") from error
    value: Any = text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            value = float(text)
        except ValueError:
            value = text
    else:
        if isinstance(parsed, dict):
            value = parsed.get("value", parsed)
            units = units or parsed.get("units") or parsed.get("unit")
        else:
            value = parsed
    return {"kind": "metric", "value": value, "units": units}


def _table(path: Path) -> dict[str, Any]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream, delimiter=delimiter))[:11]
    except (OSError, UnicodeError, csv.Error) as error:
        raise PreviewError(f"table preview failed: {error}") from error
    if not rows:
        return {"kind": "table", "columns": [], "rows": []}
    width = max(len(row) for row in rows)
    columns = rows[0] + [f"column_{index + 1}" for index in range(len(rows[0]), width)]
    values = [row + [""] * (width - len(row)) for row in rows[1:]]
    return {"kind": "table", "columns": columns, "rows": values}


def preview_artifact(
    path: str | Path,
    project_root: str | Path,
    sub_kind: str | None,
    *,
    units: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Return a JSON-safe preview without ever opening an unconfined path."""

    if max_bytes < 0:
        raise PreviewError("preview size cap cannot be negative")
    artifact, root = _safe_file(path, project_root)
    result = _base(artifact, root)
    if result["size"] > max_bytes:
        result["reason"] = "size-cap"
        return result

    suffix = artifact.suffix.lower()
    if sub_kind == "figure" and suffix in INLINE_IMAGE_TYPES:
        encoded = base64.b64encode(artifact.read_bytes()).decode("ascii")
        result.update(
            {
                "kind": "figure",
                "data_url": f"data:{INLINE_IMAGE_TYPES[suffix]};base64,{encoded}",
            }
        )
    elif sub_kind == "metric":
        result.update(_metric(artifact, units))
    elif sub_kind == "table" and suffix in {".csv", ".tsv", ".tab"}:
        result.update(_table(artifact))
    elif sub_kind == "report" and suffix in {".md", ".myst"}:
        try:
            text = artifact.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise PreviewError(f"report preview failed: {error}") from error
        result.update({"kind": "report", "text": text})
    return result
