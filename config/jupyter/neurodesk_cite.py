#!/opt/conda/bin/python3
"""Write a citation list for the Neurodesk tools this user has loaded.

The Lmod site package (config/lmod/SitePackage.lua) appends one line per
loaded Neurodesk module to the usage log. This command resolves those modules
against the neurocontainers apps.json catalogue and writes CITATIONS.md with
methods sentences and a deduplicated reference list.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

CATALOGUE_URL = "https://apps-json.scigetorg.workers.dev/neuroimaging/apps.json"
CITATION_GUIDE_URL = "https://neurodesk.org/overview/how-to-cite-us/"
RECIPES_URL = "https://github.com/neurodesk/neurocontainers/tree/main/recipes"
NEURODESK_REFERENCE = (
    "Renton, A.I., Dao, T.T., Johnstone, T., Civier, O., Sullivan, R. P., "
    "White, D. J., ... Narayanan, A. & Bollmann, S. Neurodesk: an accessible, "
    "flexible and portable data analysis environment for reproducible "
    "neuroimaging. Nat Methods (2024). https://doi.org/10.1038/s41592-023-02145-x"
)
AEDAPT_NOTE = (
    "You used EEG/MEG/electrophysiology tools. Please also cite the AEDAPT "
    'project, for example by adding "part of the AEDAPT project" to the '
    "methods sentence. See " + CITATION_GUIDE_URL
)
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
FETCH_TIMEOUT_SECONDS = 15
USER_AGENT = "neurodesk-cite"
"""The catalogue host's bot filter rejects urllib's default ``Python-urllib`` agent."""


@dataclass(frozen=True)
class ModuleUse:
    """One `module load` of a Neurodesk module, as recorded by the Lmod hook."""

    name: str
    version: str
    loaded_at: datetime


@dataclass(frozen=True)
class CatalogueEntry:
    tool: str
    build_date: date
    citation: str | None
    electrophysiology: bool


ModuleKey = tuple[str, str]
"""An Lmod module's ``(name, version)``, as recorded in the usage log."""

Catalogue = Mapping[ModuleKey, CatalogueEntry]


@dataclass(frozen=True)
class ToolCitation:
    """A tool version that was used; ``entry`` is None when the catalogue lacks it."""

    name: str
    version: str
    entry: CatalogueEntry | None

    @property
    def recipe_url(self) -> str:
        tool = self.entry.tool if self.entry is not None else self.name
        return f"{RECIPES_URL}/{tool.removesuffix('_arm64')}"

    @property
    def reference(self) -> str:
        if self.entry is not None and self.entry.citation:
            return self.entry.citation
        return (
            f"{self.name}: the Neurodesk catalogue does not list a citation yet. "
            f"See {CITATION_GUIDE_URL} and the recipe at {self.recipe_url}"
        )

    def methods_sentence(self, neurodesktop_version: str) -> str:
        built = f", built {self.entry.build_date.isoformat()}" if self.entry else ""
        return (
            f"{self.name} (v{self.version}{built}) was run in Neurodesk "
            f"(v{neurodesktop_version}, Renton et al., 2024)."
        )


def parse_usage_log(text: str) -> list[ModuleUse]:
    """Parse ``name<TAB>version<TAB>timestamp`` lines, skipping malformed ones."""
    uses = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) != 3 or not fields[0]:
            continue
        try:
            loaded_at = datetime.strptime(fields[2], TIMESTAMP_FORMAT)
        except ValueError:
            continue
        uses.append(ModuleUse(fields[0], fields[1], loaded_at.replace(tzinfo=UTC)))
    return uses


def parse_catalogue(document: object) -> dict[ModuleKey, CatalogueEntry]:
    """Index apps.json apps by the Lmod module of their image, keeping the newest build.

    An app's ``image`` is ``{name}_{version}``, and CVMFS publishes it as the
    module ``name/version``, splitting at the last underscore. Menu entries
    that share an image repeat its build date. Citation and categories belong
    to the tool.
    """
    catalogue: dict[ModuleKey, CatalogueEntry] = {}
    tools = document.items() if isinstance(document, dict) else []
    for tool_key, tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("apps"), dict):
            continue
        text = tool.get("citation")
        citation = (text.strip() or None) if isinstance(text, str) else None
        categories = tool.get("categories")
        electrophysiology = (
            isinstance(categories, list) and "electrophysiology" in categories
        )
        for app in tool["apps"].values():
            if not isinstance(app, dict):
                continue
            image, stamp = app.get("image"), app.get("version")
            if not isinstance(image, str) or not isinstance(stamp, str):
                continue
            name, _, version = image.rpartition("_")
            if not name:
                continue
            try:
                build_date = datetime.strptime(stamp, "%Y%m%d").date()
            except ValueError:
                continue
            entry = CatalogueEntry(tool_key, build_date, citation, electrophysiology)
            known = catalogue.get((name, version))
            if known is None or known.build_date < entry.build_date:
                catalogue[(name, version)] = entry
    return catalogue


def resolve(uses: Iterable[ModuleUse], catalogue: Catalogue) -> list[ToolCitation]:
    """One citation per tool version, in order of first use."""
    tools: dict[tuple[str, str], ToolCitation] = {}
    for use in uses:
        key = (use.name, use.version)
        if key not in tools:
            tools[key] = ToolCitation(use.name, use.version, catalogue.get(key))
    return list(tools.values())


def render(
    tools: Sequence[ToolCitation], neurodesktop_version: str, uses: Sequence[ModuleUse]
) -> str:
    first = min(use.loaded_at for use in uses).date().isoformat()
    last = max(use.loaded_at for use in uses).date().isoformat()
    period = f"on {first}" if first == last else f"between {first} and {last}"
    lines = [
        "# Citations for Neurodesk tools",
        "",
        f"Generated by `neurodesk-cite` from {len(uses)} recorded module loads {period}. "
        "Remove any tool your analysis did not use.",
        "",
        "## Methods",
        "",
    ]
    lines += [tool.methods_sentence(neurodesktop_version) for tool in tools]
    if any(tool.entry is not None and tool.entry.electrophysiology for tool in tools):
        lines += ["", AEDAPT_NOTE]
    references = list(dict.fromkeys(tool.reference for tool in tools))
    lines += ["", "## References", ""]
    for reference in [*references, NEURODESK_REFERENCE]:
        lines += [reference, ""]
    return "\n".join(lines)


def state_dir() -> Path:
    return (
        Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        / "neurodesk"
    )


def cache_dir() -> Path:
    return (
        Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "neurodesk"
    )


def default_output() -> Path:
    """The storage folder shared with the host, as used for persistent history."""
    for folder in (Path.home() / "neurodesktop-storage", Path("/neurodesktop-storage")):
        if folder.is_dir() and os.access(folder, os.W_OK):
            return folder / "CITATIONS.md"
    return Path.home() / "CITATIONS.md"


def write_atomically(path: Path, text: str) -> None:
    """Replace *path* in one step; the file gets the usual umask permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def load_catalogue(url: str, cache: Path) -> dict[ModuleKey, CatalogueEntry]:
    """Fetch the catalogue, refreshing the cache, or fall back to the cache."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
        catalogue = parse_catalogue(json.loads(raw))
    except (OSError, ValueError) as error:
        try:
            cached = cache.read_text(encoding="utf-8")
            catalogue = parse_catalogue(json.loads(cached))
        except (OSError, ValueError):
            print(
                f"Could not fetch {url} ({error}) and no cached catalogue exists.",
                file=sys.stderr,
            )
            print(
                "Build dates and tool citations are missing from the output.",
                file=sys.stderr,
            )
            return {}
        fetched = datetime.fromtimestamp(cache.stat().st_mtime).date().isoformat()
        print(
            f"Could not fetch {url} ({error}); using the catalogue cached on {fetched}.",
            file=sys.stderr,
        )
        return catalogue
    try:
        write_atomically(cache, raw)
    except OSError as error:
        print(f"Could not cache the catalogue in {cache}: {error}", file=sys.stderr)
    return catalogue


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="neurodesk-cite",
        description="Write CITATIONS.md for the Neurodesk tools you loaded with ml, notebooks or the menu.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="file to write (default: CITATIONS.md in neurodesktop-storage)",
    )
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="only include loads on or after this date",
    )
    parser.add_argument(
        "--clear", action="store_true", help="empty the usage log and exit"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    log = state_dir() / "module-usage.tsv"
    if args.clear:
        if log.exists():
            log.write_text("")
        print(f"Cleared {log}.")
        return 0
    try:
        uses = parse_usage_log(log.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        uses = []
    if args.since is not None:
        uses = [use for use in uses if use.loaded_at.date() >= args.since]
    if not uses:
        print(f"No Neurodesk module loads are recorded in {log}.", file=sys.stderr)
        print(
            "Load a tool with ml, a notebook or the Applications menu, then run neurodesk-cite again.",
            file=sys.stderr,
        )
        return 1
    url = os.environ.get("NEURODESKTOP_CITE_CATALOGUE_URL") or CATALOGUE_URL
    tools = resolve(uses, load_catalogue(url, cache_dir() / "apps.json"))
    version = os.environ.get("NEURODESKTOP_VERSION") or "unknown"
    output = args.output or default_output()
    try:
        write_atomically(output, render(tools, version, uses))
    except OSError as error:
        print(f"Could not write {output}: {error}", file=sys.stderr)
        return 1
    for tool in tools:
        if tool.entry is None:
            print(
                f"{tool.name}/{tool.version} is not in the Neurodesk catalogue.",
                file=sys.stderr,
            )
    print(f"Wrote citations for {len(tools)} tool versions to {output}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
