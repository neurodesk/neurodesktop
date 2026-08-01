---
title: Design records
description: Assessments, implementation plans, and audits kept as the record
  of why the tree is shaped the way it is
parent: ../index.md
status: current
last-reviewed: "2026-07-31"
---

# Design records

These documents are records, not living reference: each was accurate at its
snapshot date and is kept to explain why things are the way they are. For
current behavior see [Architecture](../architecture.md),
[Testing](../testing.md), and
[Environment variables](../environment-variables.md).

| Record | Status | What it decided |
| --- | --- | --- |
| [ASTRA and Lightcone integration](astra-lightcone-integration.md) | implemented | Adopt the ASTRA specification layer and build the read-only provenance viewer; defer Lightcone execution behind explicit upstream blockers |
| [OpenCode web interface plan](opencode-integration-plan.md) | implemented | Ship the official OpenCode web UI behind a rewriting reverse proxy with browser-based key setup |
| [Test suite audit](test-suite-audit.md) | applied | Split the suite into checkout-runnable `tests/unit/` and image-only `tests/container/` |
| [Distributed compute broker design](distributed-compute-broker.md) | proposed | Production design for JupyterHub → Forgejo Actions → site-local dispatchers → SLURM/Kubernetes with DataLad-managed data |
