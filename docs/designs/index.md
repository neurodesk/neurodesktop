---
title: Design records
description: Assessments, implementation plans, and audits kept as the record
  of why the tree is shaped the way it is
parent: ../index.md
status: current
last-reviewed: "2026-09-10"
---

# Design records

These documents are records, not living reference: each was accurate at its
snapshot date and is kept to explain why things are the way they are. For
current behavior see [Architecture](../architecture.md),
[Testing](../testing.md), and
[Environment variables](../environment-variables.md).

| Record | Status | What it decided |
| --- | --- | --- |
| [Image dependency audit, 20 September 2026](image-dependency-audit-2026-09-20.md) | assessment | Live release results, update candidates, dependency conflicts, and coverage limits |
| [Image dependency upgrade audit](image-dependency-upgrade.md) | implemented | Derive current image pins from the root Dockerfile, keep only authority and compatibility policy in the catalog, and report upstream and compatible releases separately |
| [T3 Code remote desktop assessment](t3-code-remote-assessment.md) | implemented | Let Jupyter own an optional headless T3 server and connect through T3 Connect or private networking |
| [Subscription-based agentic workflow redesign](agentic-subscription-redesign.md) | implemented | Use the existing runner and Codex subscription for issue repairs, five weekly maintenance categories, independent validation, and draft PR publication |
| [Codex authentication research](agentic-codex-auth-research.md) | research | Verify subscription CLI behavior, gh-aw authentication limits, and the explicit public-repository caveat in official CI guidance |
| [Agentic workflow reliability remediation](agentic-workflow-reliability-remediation.md) | implemented | Size hard invocation ceilings from transcripts, split diagnosis from fixing, rotate maintenance, and stop conflating scheduled infrastructure failures with agent failures |
| [ASTRA and Lightcone integration](astra-lightcone-integration.md) | implemented | Adopt the ASTRA specification layer and build the read-only provenance viewer; defer Lightcone execution behind explicit upstream blockers |
| [OpenCode web interface plan](opencode-integration-plan.md) | implemented | Ship the official OpenCode web UI behind a rewriting reverse proxy with browser-based key setup |
| [Test suite audit](test-suite-audit.md) | applied | Split the suite into checkout-runnable `tests/unit/` and image-only `tests/container/` |
| [Distributed compute broker design](distributed-compute-broker.md) | proposed | Production design for JupyterHub → Forgejo Actions → site-local dispatchers → SLURM/Kubernetes with DataLad-managed data |
