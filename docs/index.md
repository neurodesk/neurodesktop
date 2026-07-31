---
title: Neurodesktop documentation
description: Entry point for the Neurodesktop docs wiki — reference pages,
  per-subsystem architecture pages, and design records
status: current
last-reviewed: "2026-07-31"
---

# Neurodesktop documentation

Neurodesktop is a plug-and-play, browser-accessible, containerised data
analysis environment. User-facing documentation lives at
[neurodesk.org/docs/neurodesktop](https://www.neurodesk.org/docs/neurodesktop/);
this wiki documents the repository itself for contributors and agents.

## Reference

| Page | Covers |
| --- | --- |
| [Architecture](architecture.md) | Startup flow, services, directory layout, and the hub for per-subsystem pages |
| [Environment variables](environment-variables.md) | Runtime variables and Dockerfile build arguments |
| [Testing](testing.md) | The two-tier test suite, per-area focused tests, and container build/run modes |
| [Agentic maintenance workflows](agentic-maintenance.md) | The weekly agentic maintenance suite and its guardrails |

## Architecture subsystem pages

Under [`docs/architecture/`](architecture/), one page per subsystem:
[CVMFS and Neurocommand](architecture/cvmfs.md) ·
[Desktop environment](architecture/desktop.md) ·
[Webapp system](architecture/webapps.md) ·
[Workspace link routing](architecture/workspace-link-routing.md) ·
[ASTRA integration](architecture/astra.md) ·
[Coding agents](architecture/coding-agents.md) ·
[Jupyter AI](architecture/jupyter-ai.md) ·
[Agentic CI workflows](architecture/agentic-workflows.md) ·
[Build-time behaviors](architecture/build.md)

## Design records

Under [`docs/designs/`](designs/index.md): assessments, implementation plans,
and audits that explain *why* the tree is shaped the way it is. They are
records — kept accurate as of their snapshot dates, not continuously updated;
the reference pages above describe current behavior.

## Conventions

Every page carries YAML frontmatter:

```yaml
---
title: Page title
description: One- or two-line summary
parent: index.md            # relative path to the parent page
status: current             # current | proposed | implemented | applied
last-reviewed: "2026-07-31" # last time the content was verified against code
---
```

- `parent` forms the hierarchy: subsystem pages point at
  `architecture.md`, design records at `designs/index.md`, top-level pages
  here.
- `status: current` marks living reference pages; design records use
  `proposed` (not built), `implemented`/`applied` (built, kept as record).
- Cross-reference other pages and repository files with relative markdown
  links so they stay valid from any renderer.
- `docs/architecture.md`, `docs/testing.md`, and
  `docs/environment-variables.md` are referenced by path from `AGENTS.md`,
  tests, and the compiled agentic workflows — do not move or rename them.
