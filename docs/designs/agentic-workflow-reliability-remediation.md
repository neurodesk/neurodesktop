---
title: Agentic workflow reliability remediation
description: August 2026 audit of agentic runs and the controls adopted from it
parent: index.md
status: implemented
last-reviewed: "2026-08-13"
---

# Agentic workflow reliability remediation

This is the historical record for the August 13, 2026 reliability change. For
current behavior, see [Agentic CI workflows](../architecture/agentic-workflows.md)
and [Agentic maintenance workflows](../agentic-maintenance.md).

## Evidence snapshot

The audit covered all 447 agentic workflow runs since mid-June, the complete
transcripts behind issues #827 and #828, and the agentic workflow fix history.
It found 73 failed runs and 10 merged agent-authored pull requests. Active-run
failure rate improved from 86% in late June to 8% in the week of July 20, then
rose after the maintenance-fleet rollout and turn-cap hardening.

The latest failures were different classes:

- Run 31625755822 failed before any agent started when the firewall binary
  download returned HTTP 503 six times.
- Run 31670113591 reached its 30-model-invocation firewall ceiling during a
  productive abstraction investigation.
- The issue-investigator run had diagnosed issue #824, edited three scripts,
  and written a unit test when it reached 40 model invocations.

The productive runs showed no retry storm or malformed tool-call loop. Their
mandatory safe-output call was impossible after the proxy rejected the final
invocation. The generated failure path then grouped setup failures and
productive budget exhaustion under the same `agent_failure` report. Separately,
the local detector wrapper replaced an upstream CommonJS module without
re-exporting `MODEL_NOT_SUPPORTED_PATTERN`, although the Codex harness imports
that pattern; the resulting `TypeError` obscured the original cap response.

## Decision

- Raise maintenance and package-radar ceilings from 30 to 60 model invocations,
  with a 60-minute timeout and terminal-output guidance at turn 54.
- Split issue handling into a 30-invocation read-only diagnosis/comment phase
  and a manually approved 80-invocation fix/validation phase.
- Remove the eight independent agentic schedules. Dispatch exactly one member
  per week from a small conventional rotation controller and retain manual
  dispatch for canaries.
- Disable automatic gh-aw failure issues for scheduled maintenance and radar
  workflows. Their Actions results remain the source for infrastructure and
  budget telemetry.
- Preserve the upstream detector module exports in the local normalization
  wrapper and regression-test the interface expected by the Codex harness.
- Canary future compiler, harness, or model changes on one manually selected
  workflow before updating the fleet.

## Deferred external work

The repository change does not silently switch production credentials or
providers. A native-model-versus-open-weight-harness comparison needs one
bounded live canary and cost/quality evidence first. The aggregate-transcript
detector, missing budget feedback, and coarse failure categories should be
reported upstream to gh-aw; those external issues or pull requests are
intentionally not created by this repository-only change. The post-cap
`TypeError` was introduced by this repository's wrapper and is fixed here, not
misreported as an upstream defect.
