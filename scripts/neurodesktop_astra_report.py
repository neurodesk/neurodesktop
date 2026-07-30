#!/usr/bin/env python3
"""Scaffold and build a MySTRA report for an ASTRA project.

An agent that finishes an ASTRA analysis has a validated ``astra.yaml`` and no
report. The image already ships everything needed to render one -- the pinned
``myst`` CLI, the MySTRA plugin bundle, and both offline ASTRA themes -- but
wiring them together means knowing three absolute paths and MySTRA's file
discovery conventions. Left to improvisation, every project invents a slightly
different ``myst.yml`` and some of them silently fall back to a downloaded
theme.

So this owns that wiring. ``scaffold`` writes the two files MySTRA needs and
nothing else; ``build`` renders the site; ``chat-block`` prints the markdown an
agent pastes into a chat surface, with absolute workspace paths that the
``neurodesk-launcher:workspace-links`` plugin opens in the JupyterLab main
panel.

The split between ``scaffold`` and ``build`` is deliberate: scaffolding is pure
file authoring and runs anywhere, while building needs the ``myst`` CLI and the
pinned assets that only exist inside the image.

Nothing here understands the ASTRA schema. The spec summary in a chat block is
relayed verbatim from ``astra info``, so the released CLI stays the only thing
in the image that reads the schema for the report path.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


#: Pinned image assets. These are an image contract, not a search path: a
#: project that references anything else is no longer building offline against
#: the revisions this image validated.
MYSTRA_PLUGIN = Path("/opt/neurodesktop/mystra/mystra.mjs")
ASTRA_THEME_ROOT = Path("/opt/neurodesktop/astra-theme")
THEMES = ("article", "book")
DEFAULT_THEME = "article"

SPEC_NAME = "astra.yaml"
CONFIG_NAME = "myst.yml"
REPORT_NAME = "index.md"
BUILD_RELATIVE = Path("_build/html/index.html")

#: A chat message is a summary, not a transcript; cap the relayed spec summary.
INFO_LINE_CAP = 40
MYST_BUILD_TIMEOUT = 900
ASTRA_INFO_TIMEOUT = 60


class ReportError(RuntimeError):
    """Raised when the report cannot be scaffolded or built safely."""


def project_root(project_dir):
    """Resolve *project_dir* as an ASTRA project, or explain why it is not one."""
    try:
        resolved = Path(project_dir).resolve(strict=True)
    except OSError as error:
        raise ReportError(f"cannot resolve the project directory: {error}") from error
    if not resolved.is_dir():
        raise ReportError(f"not a directory: {resolved}")
    if not (resolved / SPEC_NAME).is_file():
        raise ReportError(
            f"no {SPEC_NAME} in {resolved}; a MySTRA report needs an ASTRA spec "
            "to resolve its embeds against"
        )
    return resolved


def _write_new_text(destination, content):
    """Write *content* atomically, leaving an existing file untouched.

    Returns ``True`` when the file was created. An author's edits outlive every
    later run of this command, so an existing file is never rewritten and never
    treated as an error.
    """
    if destination.exists():
        return False

    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(0o644)
        # os.replace would clobber a file that appeared since the check above.
        os.link(temporary_path, destination)
    except FileExistsError:
        return False
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return True


def myst_config(theme):
    """The ``myst.yml`` that opts into the pinned plugin and one local theme."""
    if theme not in THEMES:
        raise ReportError(f"unknown theme {theme!r}; choose one of {', '.join(THEMES)}")
    return f"""version: 1
project:
  plugins:
    - {MYSTRA_PLUGIN}
  toc:
    - file: {REPORT_NAME}
site:
  template: {ASTRA_THEME_ROOT / theme}
"""


def starter_report(name):
    """A report that references the analysis rather than restating it.

    Collection-level embeds are used deliberately: they stay correct as the spec
    grows, so a scaffold does not rot into a list of ids that no longer exist.
    """
    return f"""# {name}

<!-- Scaffolded by neurodesktop-astra-report. This file is yours: once it
exists it is never rewritten. Each embed below resolves from astra.yaml and the
active universe at build time, so prose written here stays true as the analysis
changes. Narrow an embed to one element with, for example,
`:::{{astra}} decisions.<id>`, and mention elements inline with
`{{astra}}`decisions.<id>``. -->

## Inputs

:::{{astra}} inputs
:::

## Decisions

:::{{astra}} decisions
:::

## Outputs

:::{{astra}} outputs
:::

## Findings

:::{{astra}} findings
:::
"""


def scaffold(root, theme):
    """Create the two files MySTRA needs, keeping anything already authored."""
    created = []
    if _write_new_text(root / CONFIG_NAME, myst_config(theme)):
        created.append(CONFIG_NAME)
    if _write_new_text(root / REPORT_NAME, starter_report(root.name)):
        created.append(REPORT_NAME)
    return created


def _require_build_assets():
    """Fail before invoking MyST rather than after a confusing theme download."""
    missing = [] if MYSTRA_PLUGIN.is_file() else [str(MYSTRA_PLUGIN)]
    # Check both flavors: an existing myst.yml may name either one.
    missing += [
        str(ASTRA_THEME_ROOT / theme)
        for theme in THEMES
        if not (ASTRA_THEME_ROOT / theme / "template.yml").is_file()
    ]
    if missing:
        raise ReportError(
            "the pinned MySTRA plugin and ASTRA themes are not installed: "
            + ", ".join(missing)
        )
    if shutil.which("myst") is None:
        raise ReportError("the myst CLI is not on PATH")


def _build_env():
    """Force the theme server onto IPv4 loopback for the export crawl.

    A static export starts the ASTRA theme's own server and crawls it. The
    theme binds ``process.env.HOST || 'localhost'``, and this image resolves
    ``localhost`` to ``::1`` first, so Node binds IPv6 only -- while MyST's
    crawler connects to ``127.0.0.1``. The two halves of one toolchain then
    disagree about what ``localhost`` means and every fetch is refused, which
    surfaces as a build that reports a started server and then fails.

    An explicit HOST from the caller wins, so this can still be overridden.
    """
    env = os.environ.copy()
    env.setdefault("HOST", "127.0.0.1")
    return env


def build(root, theme):
    """Scaffold if needed, then render the site into ``_build/html``."""
    created = scaffold(root, theme)
    _require_build_assets()
    try:
        completed = subprocess.run(
            ["myst", "build", "--html"],
            cwd=root,
            env=_build_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=MYST_BUILD_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReportError(f"myst build could not run: {error}") from error
    if completed.returncode != 0:
        raise ReportError(f"myst build failed:\n{completed.stdout.strip()}")
    if not (root / BUILD_RELATIVE).is_file():
        raise ReportError(
            f"myst build reported success but produced no {BUILD_RELATIVE}"
        )
    return created


def _astra_info(root):
    """Relay ``astra info``, or nothing at all.

    Best-effort by design: a chat block is still useful without a summary, and
    this must never become a second reader of the ASTRA schema.
    """
    if shutil.which("astra") is None:
        return None
    try:
        completed = subprocess.run(
            ["astra", "info"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=ASTRA_INFO_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    lines = completed.stdout.strip().splitlines()
    if not lines:
        return None
    if len(lines) > INFO_LINE_CAP:
        lines = lines[:INFO_LINE_CAP] + ["... (truncated; run `astra info` for all)"]
    return "\n".join(lines)


def _markdown_link(path):
    """Link an absolute workspace path so a chat renderer keeps it intact.

    Percent-encoding matters: the click interceptor reads ``url.pathname`` and
    decodes it, so a space or a parenthesis in a project name survives the round
    trip instead of truncating the link.
    """
    return f"[{path}]({quote(str(path), safe='/')})"


def chat_block(root):
    """The markdown an agent pastes into a chat surface.

    Every target is an absolute path inside the workspace, which is exactly what
    ``neurodesk-launcher:workspace-links`` claims and opens in the main panel.
    """
    built = root / BUILD_RELATIVE
    lines = [f"**ASTRA report — {root.name}**", ""]
    if built.is_file():
        lines.append(f"- Rendered report: {_markdown_link(built)}")
    else:
        lines.append(
            "- Rendered report: not built yet "
            "(`neurodesktop-astra-report build --project-dir .`)"
        )
    lines.append(f"- Report source: {_markdown_link(root / REPORT_NAME)}")
    lines.append(f"- ASTRA spec: {_markdown_link(root / SPEC_NAME)}")

    summary = _astra_info(root)
    if summary:
        lines += ["", "```", summary, "```"]
    return "\n".join(lines) + "\n"


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="neurodesktop-astra-report",
        description="Scaffold and build a MySTRA report for an ASTRA project.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser(
        "scaffold",
        help="write myst.yml and index.md, keeping anything already authored",
    )
    build_parser = subparsers.add_parser(
        "build",
        help="scaffold if needed, render the site, and print the chat block",
    )
    chat_parser = subparsers.add_parser(
        "chat-block",
        help="print the chat markdown for a project without building",
    )
    for subparser in (scaffold_parser, build_parser, chat_parser):
        subparser.add_argument(
            "--project-dir",
            default=".",
            type=Path,
            help="the ASTRA project directory (default: the working directory)",
        )
    for subparser in (scaffold_parser, build_parser):
        subparser.add_argument(
            "--theme",
            choices=THEMES,
            default=DEFAULT_THEME,
            help="report presentation flavor written into a new myst.yml",
        )
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        root = project_root(args.project_dir)
        if args.command == "scaffold":
            created = scaffold(root, args.theme)
            print(f"scaffolded: {root} (created: {', '.join(created) or 'nothing'})")
            return 0
        if args.command == "build":
            build(root, args.theme)
            print(f"built: {root / BUILD_RELATIVE}\n")
            print(chat_block(root), end="")
            return 0
        if args.command == "chat-block":
            print(chat_block(root), end="")
            return 0
    except (OSError, ReportError) as error:
        print(f"report failed: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
