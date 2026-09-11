#!/usr/bin/env python3
"""Audit direct version declarations in the root Neurodesktop image.

The Dockerfile owns current versions. This script owns only package identity,
release authority, and compatibility policy. It deliberately audits supported
direct declarations, not resolver-selected transitive packages in a built
image; see docs/designs/image-dependency-upgrade.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class CatalogEntry:
    locator: str
    key: str
    authority: str
    constraint: str | None = None
    why: str = ""


@dataclass(frozen=True)
class Declaration:
    locator: str
    current: str
    source: str


# Current versions are intentionally absent. ARG and requirement locators are
# semantic, so moving a declaration within the Dockerfile does not change this
# table. Authority values are interpreted only by fetch_live_releases().
CATALOG = (
    CatalogEntry("arg:BASE_IMAGE_TAG", "oci:quay.io/jupyter/base-notebook", "quay"),
    CatalogEntry("arg:APPTAINER_VERSION", "github:apptainer/apptainer", "github-releases"),
    CatalogEntry("arg:APPTAINER_GO_VERSION", "golang:go", "golang"),
    CatalogEntry("arg:APPTAINER_GRPC_VERSION", "go:google.golang.org/grpc", "go-proxy"),
    CatalogEntry("arg:APPTAINER_CRYPTO_VERSION", "go:golang.org/x/crypto", "go-proxy"),
    CatalogEntry("arg:CVMFS_VERSION", "github:cvmfs/cvmfs", "github-tags"),
    CatalogEntry("arg:CVMFS_RELEASE_VERSION", "manual:cvmfs-release", "manual"),
    CatalogEntry("arg:NPM_VERSION", "npm:npm", "npm"),
    CatalogEntry("arg:JUPYTER_BUILDER_VERSION", "npm:@jupyter/builder", "npm"),
    CatalogEntry("arg:GUACAMOLE_VERSION", "github:apache/guacamole-server", "github-tags"),
    CatalogEntry("arg:CODE_SERVER_VERSION", "github:coder/code-server", "github-releases"),
    CatalogEntry("arg:TOMCAT_VERSION", "apache:tomcat/tomcat-11", "apache-dist", "<12"),
    CatalogEntry("arg:TOMCAT_MIGRATION_VERSION", "github:apache/tomcat-jakartaee-migration", "github-tags"),
    CatalogEntry("arg:UV_VERSION", "pypi:uv", "pypi"),
    CatalogEntry("arg:JUPYTER_AI_VERSION", "pypi:jupyter-ai", "pypi"),
    CatalogEntry(
        "arg:JUPYTER_COLLABORATION_VERSION",
        "pypi:jupyter-collaboration",
        "pypi",
        "<5",
        "Jupyter AI 3.2 requires jupyter-collaboration below 5.",
    ),
    CatalogEntry("arg:ASTRA_SPEC_VERSION", "pypi:astra-spec", "pypi"),
    CatalogEntry("arg:ASTRA_TOOLS_VERSION", "pypi:astra-tools", "pypi"),
    CatalogEntry("arg:ANYWIDGET_VERSION", "pypi:anywidget", "pypi"),
    CatalogEntry("arg:IPYNIIVUE_VERSION", "pypi:ipyniivue", "pypi"),
    CatalogEntry("arg:SNAKEMAKE_VERSION", "pypi:snakemake", "pypi"),
    CatalogEntry("arg:NBI_JUPYTERLAB_BUILDER_VERSION", "npm:@jupyterlab/builder", "npm"),
    CatalogEntry(
        "arg:MYST_PNPM_VERSION",
        "npm:pnpm",
        "npm",
        "<12",
        "The MyST source build uses its pnpm 11 lockfile/toolchain.",
    ),
    CatalogEntry("arg:MYST_YDOC_VERSION", "npm:@jupyter/ydoc", "npm"),
    CatalogEntry("arg:CODEX_CLI_VERSION", "npm:@openai/codex", "npm"),
    CatalogEntry("arg:T3_CODE_VERSION", "npm:t3", "npm"),
    CatalogEntry("arg:CLAUDE_CODE_VERSION", "npm:@anthropic-ai/claude-code", "npm"),
    CatalogEntry("arg:OPENCODE_VERSION", "github:anomalyco/opencode", "github-releases"),
    CatalogEntry("arg:CODEX_ACP_VERSION", "npm:@agentclientprotocol/codex-acp", "npm"),
    CatalogEntry("arg:CLAUDE_AGENT_ACP_VERSION", "npm:@agentclientprotocol/claude-agent-acp", "npm"),
    CatalogEntry("arg:LIGHTCONE_CLI_VERSION", "pypi:lightcone-cli", "pypi"),
    CatalogEntry("arg:NODE_TAR_VERSION", "npm:tar", "npm"),
    CatalogEntry(
        "requirement:pypi:pydra",
        "pypi:pydra",
        "pypi",
        ">=1.0a0,<1.0rc0",
        "The image intentionally follows Pydra's 1.0 prerelease line.",
    ),
    CatalogEntry("requirement:pypi:jupyter-server-proxy", "pypi:jupyter-server-proxy", "pypi"),
    CatalogEntry(
        "requirement:pypi:notebook-intelligence",
        "pypi:notebook-intelligence",
        "pypi",
        "==5.3.1",
        "Notebook Intelligence 5.4.0 conflicts with the ACP client's protocol dependency.",
    ),
    CatalogEntry(
        "requirement:pypi:mcp",
        "pypi:mcp",
        "pypi",
        "<2",
        "Notebook Intelligence still imports MCP's v1 FastMCP API.",
    ),
    CatalogEntry("requirement:pypi:jupyter-ai-acp-client", "pypi:jupyter-ai-acp-client", "pypi"),
    CatalogEntry("requirement:pypi:jupyter-ai-chat-commands", "pypi:jupyter-ai-chat-commands", "pypi"),
    CatalogEntry("requirement:pypi:jupyter-ai-persona-manager", "pypi:jupyter-ai-persona-manager", "pypi"),
    CatalogEntry("requirement:pypi:jupyter-ai-router", "pypi:jupyter-ai-router", "pypi"),
    CatalogEntry("requirement:pypi:jupyter-ai-tools", "pypi:jupyter-ai-tools", "pypi"),
    CatalogEntry("requirement:pypi:jupyter-server-documents", "pypi:jupyter-server-documents", "pypi"),
    CatalogEntry("requirement:pypi:jupyter-server-mcp", "pypi:jupyter-server-mcp", "pypi"),
    CatalogEntry("requirement:pypi:jupyterlab-chat", "pypi:jupyterlab-chat", "pypi"),
    CatalogEntry("requirement:pypi:jupyterlab-commands-toolkit", "pypi:jupyterlab-commands-toolkit", "pypi"),
    CatalogEntry("requirement:pypi:jupyterlab-notebook-awareness", "pypi:jupyterlab-notebook-awareness", "pypi"),
    CatalogEntry("requirement:pypi:jupyterlab-niivue", "pypi:jupyterlab-niivue", "pypi"),
    CatalogEntry("requirement:pypi:jupyterlab-myst", "pypi:jupyterlab-myst", "pypi"),
    CatalogEntry(
        "requirement:pypi:ipykernel",
        "pypi:ipykernel",
        "pypi",
        "<7",
        "Stable comm behavior is required until per-target subshells are production-ready.",
    ),
    CatalogEntry("requirement:pypi:ipywidgets", "pypi:ipywidgets", "pypi"),
    CatalogEntry("requirement:pypi:jupyterlab-widgets", "pypi:jupyterlab-widgets", "pypi"),
    CatalogEntry(
        "requirement:pypi:packaging",
        "pypi:packaging",
        "pypi",
        "<26",
        "Snakemake 9.26.1 requires packaging below 26.",
    ),
    CatalogEntry("requirement:pypi:requests", "pypi:requests", "pypi"),
    CatalogEntry("requirement:pypi:litellm", "pypi:litellm", "pypi"),
    CatalogEntry("requirement:pypi:chardet", "pypi:chardet", "pypi"),
)


IGNORED_VERSION_ARGS = {"NEURODESKTOP_VERSION"}
ARG_RE = re.compile(r"^\s*ARG\s+([A-Z][A-Z0-9_]*(?:VERSION|_TAG))=(?:\"([^\"]+)\"|'([^']+)'|([^\s#]+))")
REQUIREMENT_RE = re.compile(
    r"(?<![<A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]*)"
    r"((?:==|>=|<=|~=|!=|>|<)[0-9][A-Za-z0-9.*+!_,<>=~-]*)"
)


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def discover(dockerfile: Path) -> tuple[list[Declaration], list[str]]:
    found: dict[str, list[Declaration]] = {}
    text = dockerfile.read_text(encoding="utf-8")
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        arg_match = ARG_RE.match(line)
        if arg_match:
            name = arg_match.group(1)
            if name not in IGNORED_VERSION_ARGS:
                value = next(value for value in arg_match.groups()[1:] if value is not None)
                declaration = Declaration(f"arg:{name}", value, f"{dockerfile}:{line_number}")
                found.setdefault(declaration.locator, []).append(declaration)
        for requirement in REQUIREMENT_RE.finditer(line):
            name, requested = requirement.groups()
            declaration = Declaration(
                f"requirement:pypi:{normalize_name(name)}",
                requested,
                f"{dockerfile}:{line_number}",
            )
            found.setdefault(declaration.locator, []).append(declaration)

    declarations: list[Declaration] = []
    errors: list[str] = []
    catalog_by_locator = {entry.locator: entry for entry in CATALOG}
    for locator, occurrences in sorted(found.items()):
        values = {item.current for item in occurrences}
        if len(values) > 1:
            locations = ", ".join(item.source for item in occurrences)
            errors.append(f"conflicting values for {locator} at {locations}")
            continue
        if locator not in catalog_by_locator:
            label = locator.removeprefix("arg:") if locator.startswith("arg:") else locator
            kind = "ARG " if locator.startswith("arg:") else ""
            errors.append(
                f"untracked direct version declaration {kind}{label} at {occurrences[0].source}"
            )
            continue
        declarations.append(occurrences[0])
    return declarations, errors


def request_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "neurodesktop-image-audit/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_live_releases(entry: CatalogEntry) -> list[str] | None:
    identity = entry.key.split(":", 1)[1]
    if entry.authority == "manual":
        return None
    if entry.authority == "pypi":
        data = request_json(f"https://pypi.org/pypi/{urllib.parse.quote(identity)}/json")
        return list(data["releases"])
    if entry.authority == "npm":
        data = request_json(f"https://registry.npmjs.org/{urllib.parse.quote(identity, safe='')}")
        return list(data.get("versions", {}))
    if entry.authority in {"github-releases", "github-tags"}:
        endpoint = "releases" if entry.authority == "github-releases" else "tags"
        data = request_json(f"https://api.github.com/repos/{identity}/{endpoint}?per_page=100")
        field = "tag_name" if endpoint == "releases" else "name"
        return [item[field] for item in data if field in item]
    if entry.authority == "go-proxy":
        encoded = identity.replace("!", "!!")
        url = f"https://proxy.golang.org/{encoded}/@v/list"
        request = urllib.request.Request(url, headers={"User-Agent": "neurodesktop-image-audit/1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8").splitlines()
    if entry.authority == "golang":
        data = request_json("https://go.dev/dl/?mode=json&include=all")
        return [item["version"].removeprefix("go") for item in data if item.get("stable")]
    if entry.authority == "quay":
        data = request_json(
            "https://quay.io/api/v1/repository/jupyter/base-notebook/tag/?onlyActiveTags=true&limit=100"
        )
        return [item["name"] for item in data.get("tags", []) if re.fullmatch(r"20\d\d-\d\d-\d\d", item.get("name", ""))]
    if entry.authority == "apache-dist":
        request = urllib.request.Request(
            f"https://downloads.apache.org/{identity}/",
            headers={"User-Agent": "neurodesktop-image-audit/1"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            listing = response.read().decode("utf-8")
        return re.findall(r'href="v([0-9][0-9.]*)/"', listing)
    raise ValueError(f"unsupported authority {entry.authority} for {entry.key}")


def clean_release(raw: str) -> str:
    value = raw.strip()
    for prefix in (
        "tomcat-jakartaee-migration-",
        "guacamole-server-",
        "cvmfs-",
        "release-",
        "tomcat-",
        "v",
        "go",
    ):
        if value.startswith(prefix) and len(value) > len(prefix):
            value = value[len(prefix) :]
            break
    return value.replace("_", ".")


def ordered_releases(releases: Iterable[str]) -> list[tuple[Version, str]]:
    parsed: dict[Version, str] = {}
    for raw in releases:
        cleaned = clean_release(raw)
        try:
            comparable = cleaned.replace("-", ".") if re.fullmatch(r"20\d\d-\d\d-\d\d", cleaned) else cleaned
            version = Version(comparable)
        except InvalidVersion:
            continue
        if version.is_devrelease:
            continue
        parsed[version] = cleaned
    return sorted(parsed.items())


def requested_spec(current: str) -> SpecifierSet:
    if current[0].isdigit():
        exact = current.replace("-", ".") if re.fullmatch(r"20\d\d-\d\d-\d\d", current) else current
        return SpecifierSet(f"=={exact}")
    return SpecifierSet(current)


def assess(entry: CatalogEntry, declaration: Declaration, releases: list[str]) -> dict[str, str | None]:
    ordered = ordered_releases(releases)
    if not ordered:
        raise ValueError(f"no comparable releases for {entry.key}")
    stable = [item for item in ordered if not item[0].is_prerelease]
    upstream_version, upstream_text = (stable or ordered)[-1]
    constraint = SpecifierSet(entry.constraint or "")
    compatible = [
        item for item in ordered if constraint.contains(item[0], prereleases=True)
    ]
    if not compatible:
        raise ValueError(f"no release for {entry.key} satisfies {entry.constraint}")
    stable_compatible = [item for item in compatible if not item[0].is_prerelease]
    compatible_version, compatible_text = (stable_compatible or compatible)[-1]

    current_spec = requested_spec(declaration.current)
    exact = declaration.current[0].isdigit() or declaration.current.startswith("==")
    if exact:
        raw_current = declaration.current.removeprefix("==")
        comparable_current = (
            raw_current.replace("-", ".")
            if re.fullmatch(r"20\d\d-\d\d-\d\d", raw_current)
            else raw_current
        )
        current_version = Version(comparable_current)
        if compatible_version > current_version:
            status = "compatible-update-available" if entry.constraint else "update-available"
        elif upstream_version > current_version and entry.constraint:
            status = "held"
        elif upstream_version > current_version:
            status = "update-available"
        else:
            status = "current"
    else:
        lower_bounds = [
            Version(spec.version)
            for spec in current_spec
            if spec.operator in {">", ">=", "~="}
        ]
        if compatible_version not in current_spec:
            status = "constraint-excludes-compatible"
        elif lower_bounds and compatible_version > max(lower_bounds):
            status = "compatible-update-available"
        elif upstream_version not in current_spec:
            status = "held"
        else:
            status = "current"
    return {
        "current": declaration.current,
        "key": entry.key,
        "latest_compatible": compatible_text,
        "latest_upstream": upstream_text,
        "source": declaration.source,
        "status": status,
        "why": entry.why,
    }


def audit(dockerfile: Path, fixtures: dict[str, list[str]] | None) -> tuple[list[dict], list[str]]:
    declarations, errors = discover(dockerfile)
    catalog_by_locator = {entry.locator: entry for entry in CATALOG}
    rows: list[dict] = []
    for declaration in declarations:
        entry = catalog_by_locator[declaration.locator]
        try:
            if fixtures is not None:
                if entry.key not in fixtures:
                    errors.append(f"missing offline releases for {entry.key}")
                    continue
                releases = fixtures[entry.key]
            else:
                releases = fetch_live_releases(entry)
                if releases is None:
                    rows.append(
                        {
                            "current": declaration.current,
                            "key": entry.key,
                            "latest_compatible": None,
                            "latest_upstream": None,
                            "source": declaration.source,
                            "status": "manual-review",
                            "why": entry.why,
                        }
                    )
                    continue
            rows.append(assess(entry, declaration, releases))
        except Exception as exc:  # keep independent registry failures visible
            errors.append(f"{entry.key}: {exc}")
    return sorted(rows, key=lambda row: row["key"]), sorted(errors)


def render_text(rows: list[dict], errors: list[str]) -> str:
    lines = []
    for row in rows:
        lines.append(
            f"{row['status'].upper():27} {row['key']} {row['current']} -> "
            f"{row['latest_compatible'] or '?'} (upstream {row['latest_upstream'] or '?'})"
        )
    lines.extend(f"ERROR                       {error}" for error in errors)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, default=Path("Dockerfile"))
    parser.add_argument("--offline-fixtures", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    fixtures = None
    if args.offline_fixtures:
        fixtures = json.loads(args.offline_fixtures.read_text(encoding="utf-8"))
    rows, errors = audit(args.dockerfile, fixtures)
    if args.format == "json":
        print(json.dumps({"dependencies": rows, "errors": errors}, indent=2, sort_keys=True))
    else:
        print(render_text(rows, errors))
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
