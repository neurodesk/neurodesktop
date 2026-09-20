---
title: Image packaging and Lightcone environment audit
description: Packaging cleanup, September package updates, and a dependency
  comparison for moving Lightcone into the main Python environment
parent: index.md
status: implemented
last-reviewed: "2026-09-20"
---

# Image packaging and Lightcone environment audit

This record covers the September 20 packaging changes. Current behavior lives
in [Build-time behaviors](../architecture/build.md) and
[ASTRA integration](../architecture/astra.md).

## Packaging changes

The Dockerfile removes superseded NBI, MyST, collaboration, and document-provider
wheel assets before committing the pip layer. MyST exposes its rebuilt package
bundle through a symlink instead of a second copy. Guacamole's WAR is extracted
and removed in the download layer. The pip layer installs and purges both
`libgpgme-dev` and `libossp-uuid-dev`. Apptainer's builder removes unused CNI
plugins before the runtime copy.

These changes retain the installed features. Layer tests check the placement of
cleanup because a file's absence in a running container does not establish that
its bytes are absent from image history.

## Version updates

The live [version auditor](../../scripts/audit_image_versions.py) reports the
following selected releases as current on September 20:

| Component | Previous | Selected |
| --- | --- | --- |
| Jupyter base image | 2026-09-07 | 2026-09-18 |
| code-server | 4.136.2 | 4.138.0 |
| Tomcat | 11.0.25 | 11.0.26 |
| OpenCode | 1.18.30 | 1.18.31 |
| Codex ACP | 1.11.0 | 1.12.0 |
| Claude ACP | 0.76.0 | 0.79.0 |
| Snakemake | 9.26.1 | 9.27.0 |
| uv | 0.12.12 | 0.12.17 |

Codex ACP 1.12.0 declares `@openai/codex ^0.154.0`, which excludes the image's
0.155.1. Using the image binary remains a deliberate dependency-range exception.
The installed-image ACP initialization test and both fresh and legacy-default
T3 provider probes must pass before this combination is considered validated.
Claude ACP 0.79.0 pins `@anthropic-ai/claude-agent-sdk` to 0.3.274. Its platform
packages are removed as before, and the adapter uses the image-owned Claude
2.1.278 through `CLAUDE_CODE_EXECUTABLE`.

The adapter declarations were checked against the published
[Codex ACP manifest](https://registry.npmjs.org/@agentclientprotocol/codex-acp/1.12.0)
and [Claude ACP manifest](https://registry.npmjs.org/@agentclientprotocol/claude-agent-acp/0.79.0).

## Lightcone can resolve with the main dependencies

Lightcone 0.4.2 does not require its own Python interpreter. The existing `uv`
tool environment already reuses `/opt/conda/bin/python` with
`--no-python-downloads`. Its cost is the separate installed dependency tree,
including ASTRA and Snakemake.

The starting dependency list is published in
[Lightcone 0.4.2's package metadata](https://pypi.org/pypi/lightcone-cli/0.4.2/json).

The verified Lightcone source archive was patched with the existing
[ASTRA compatibility patch](../../config/agents/patch_lightcone_cli.py).
Two `pip install --dry-run --upgrade --report` solves ran against the new
Python 3.13 base: one with the Dockerfile's main Python requirements and one
with the same requirements plus patched Lightcone. Both included the later
LiteLLM requirement. The pinned Slurm source build was represented by its
declared runtime requirement, `jupyter_server>=2.0.1,<3`; this was a dependency
comparison, not a frontend build.

Both solves succeeded. Adding Lightcone changed no versions in the baseline
solve and removed no packages. It added only these distributions:

| Distribution | Resolved version |
| --- | --- |
| lightcone-cli | 0.4.2 |
| dask | 2026.8.0 |
| dask-gateway | 2026.3.0 |
| distributed | 2026.8.0 |
| locket | 1.0.0 |
| partd | 1.4.2 |
| sortedcontainers | 2.4.0 |
| tblib | 3.2.2 |
| toolz | 1.1.0 |
| zict | 3.0.0 |

There is no observed resolver conflict requiring isolation. This does not yet
prove that shared runtime state, Dask workers, or a real Slurm job work.

The built image's isolated environment occupies 153,491,797 bytes, about
146 MiB, and contains 108 distributions. After normalizing distribution names,
98 also exist in the main environment. Nine shared distributions have different
versions in the two independent solves, including Tornado, SQLAlchemy, and
wrapt. This reinforces the need to constrain a shared install to the tested
main environment. The measured directory size is not the net saving: the
main environment would still need the ten additions above.

## Migration path

Keep the archive checksum and source patch. Install patched Lightcone into
`/opt/conda` with constraints taken from the already installed main environment,
so the later tool layer cannot silently replace Jupyter's dependencies. Remove
the `uv tool install` and its environment in the same change. Do not retain the
old tree or use `--system-site-packages`, which would keep two package lookup
paths and make runtime ownership less clear.

Change the [Slurm template](../../config/slurm/astra_lc_run.sbatch) to prepend
`/opt/conda/bin`, and migrate its unit and image tests together. Confirm that
`lc`, `dask`, `snakemake`, and `astra` all resolve from that directory. Run
`pip check`, `lc init`, the example's `lc status --json` viewer round trip,
a local Dask task, and a real Slurm execution. Re-run Jupyter, NBI, widgets,
and ACP checks to detect effects beyond the command-line tools.

Measure the removed `/opt/uv/tools/lightcone-cli` tree and the added main-site
packages, then compare image layers. No size saving is claimed from the
resolver result alone. The isolated environment remains in this change while
the investigation establishes this migration path.

## Validation

All 777 checkout unit tests pass under the new base's Python 3.13 with CI's
local-extension dependencies, Git, curl, and an executable clean temporary
directory. The host's Python 3.10 cannot collect the existing `tomllib` test.
The live version audit also passes. The amd64 image builds successfully, and
the selected base tag advertises both amd64 and arm64 manifests. An arm64 build
was not run.

The installed-image selection passes 67 tests with one expected skip for the
inactive `GRANT_SUDO=no` mode. It covers packaging, Guacamole settings, the
Codex/Claude/OpenCode ACP handshakes, both T3 provider defaults, NBI, Jupyter
extension compatibility, RISE, widget replay, Lightcone status, and agent skills.
The headless runner cannot create a WebGL2 context, so the browser replay test
runs without NiiVue rendering. Both the main Python environment's `pip check`
and the isolated environment's `uv pip check` pass. Runtime version commands
confirm the selected code-server, Tomcat, OpenCode, Snakemake, and uv releases.

The image is 7,356,700,160 bytes as reported by Docker. There is no equivalent
baseline build for a measured before/after comparison. Dive inspected all 80
filesystem layers and reported 185,046,552 inefficient bytes with a 98.22%
efficiency score. Those are whole-image statistics, not savings attributable
to this change.

Inspection of Dive's per-layer file lists confirms that no layer contains
`ROOT.war`, Apptainer's CNI tree, `gpgme.h`, or the OSSP UUID header. The main
pip layer contains none of the superseded frontend payloads. The NBI and MyST
application paths are symlinks, with no regular-file payloads below those paths
in any layer. This checks the image history as well as the running filesystem.

The local validation artifacts are under
`/storage/tmp/neurodesktop-packaging-audit`: `unit-tests-verified.log`,
`image-tests.xml`, the three package inventories, `lightcone-inventory.json`,
`dive.json`, and `layer-checks.json`. The built image is tagged
`neurodesktop:packaging-audit`.
