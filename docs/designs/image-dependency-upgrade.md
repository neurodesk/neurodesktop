---
title: Image dependency upgrade audit
description: Design record for auditing and upgrading direct image dependencies
parent: index.md
status: implemented
last-reviewed: "2026-09-10"
---

# Image dependency upgrade audit

The root `Dockerfile` remains the source of truth for versions installed in the
Neurodesktop image. The read-only `scripts/audit_image_versions.py` command
extracts current versions from that file and joins them to a small catalog of
package identities, authoritative release sources, and compatibility holds.
The catalog never repeats a current version.

Exact declarations outside a catalog compatibility range are errors, including
versions below a supported minimum.

The auditor reports the latest upstream release and the latest release allowed
by a compatibility constraint separately. This distinction keeps an MCP 2.x
hold from hiding an available MCP 1.x update. Offline fixtures exercise the
same comparison path without network access, and missing fixture data is an
error rather than a reason to fall back to live lookups.

Discovery intentionally covers version arguments and exact or ranged Python
requirements in the root image. A new declaration in those supported forms
fails until it has an authority owner. Mutable installers and resolver-selected
transitive packages remain outside declaration freshness: a fresh image build,
installed inventory, `pip check`, extension validation, and the container test
suite prove the resulting image instead.

The design uses semantic locators such as `ARG UV_VERSION` and
`pypi:jupyter-ai`; line numbers are diagnostics only. Reformatting the
Dockerfile therefore does not require catalog churn, while conflicting repeated
arguments still fail visibly.

Direct-package conflicts are represented as explicit holds rather than hidden
resolver backtracking. For example, Snakemake stays at the current user-facing
release in both environments that install it while its `packaging<26`
dependency is held at the newest compatible version.

Installer-driven CLIs are versioned too. Claude Code is installed through its
native installer at an audited exact version, while the audit uses the official
npm release stream as the machine-readable version authority.
