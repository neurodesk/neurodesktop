# AGENTS.md

## Delivery workflow

- Keep changes scoped to the requested work.
- For every task that changes the repository, commit and push a branch and open
  a pull request, or update the existing PR for that work. Include the change
  summary and validation results. Opening the PR is required, not an optional
  follow-up.
- Stay with the PR until review and CI finish. Read all review-bot comments,
  including inline threads and review summaries, after each push. Incorporate
  every actionable comment, test the changes, and push the fixes. For an
  incorrect or inapplicable comment, reply with evidence instead of silently
  dismissing it. Resolve threads only after addressing their feedback.
- Run the full unit suite and the checks required for the changed subsystem in
  [testing](docs/testing.md). Before merging, verify that all applicable tests
  and CI checks pass on the latest PR commit, bot reviews have completed, all
  feedback is addressed, and required approvals are satisfied. Pending,
  cancelled, or missing required checks are not passing checks.
- Once those conditions hold and the PR is mergeable, merge it without asking
  for another confirmation. Respect branch protection and required reviews.
  If credentials, infrastructure, failed checks, or unresolved review feedback
  block completion, leave the PR open and report the specific blocker.
- End with the PR link, validation results, and whether it merged or remains
  open. If access prevents creating a PR, report that explicitly.

## Tests and documentation

- Test bug fixes before reporting success. Add appropriate tests for new
  features and update the affected docs. Update AGENTS.md only when agent
  instructions change.
- Before adding or changing tests, read [testing](docs/testing.md) for tier
  selection, subject resolution, runtime prerequisites, and negative-test rules.
  Default to `tests/unit/`; use `tests/container/` when a running image is needed.
  Test behavior by executing the subject; reserve source assertions for
  packaging contracts.
- When editing docs, follow the [wiki conventions](docs/index.md#conventions).
  Keep current behavior in reference pages and historical records under
  [docs/designs/](docs/designs/index.md).
- For startup flow, directory layout, or subsystem changes, read
  [architecture](docs/architecture.md) and its relevant subsystem page.
  For runtime or build variables, read
  [environment variables](docs/environment-variables.md).
- Preserve the paths `docs/architecture.md`, `docs/testing.md`, and
  `docs/environment-variables.md`; tests and agent workflows reference them.

## Subsystem safeguards

- When changing the root image's base tag or direct package pins, run
  `python scripts/audit_image_versions.py` for the live release report and
  `pytest tests/unit/test_audit_image_versions.py`. The Dockerfile owns current
  versions; keep only package authority and compatibility policy in the audit
  catalog. A complete upgrade still requires a built-image inventory and the
  subsystem checks selected by the changed packages.
- T3 Connect changes must preserve Jupyter authentication/XSRF, keep device codes
  transient and OAuth credentials out of browser responses/logs, and distinguish
  a saved link from a reachable tunnel. Cover cancellation, active-chat restart
  protection, expired authorization, and delayed relay routing. T3 provider tests
  must exercise the initialization probe with the image-owned provider path
  for both fresh modern defaults and migration of the exact legacy default.

- Coding-agent CLI upgrades must pass the installed-image T3 and ACP initialization
  probes; check adapter dependency ranges and record any deliberate exception.

- Preserve the package-only sudo default and private VS Code socket. For startup
  privilege or desktop credential changes, follow
  [the security contract](docs/architecture/desktop.md#credentials-and-service-access)
  and run `tests/unit/test_startup_security.py` plus the installed-image checks
  in `tests/container/test_security_policy.py`. Keep root initialization outside
  the notebook user's sudo allowlist. Provider credential injection must validate
  the exact HTTPS authority, including during endpoint changes.

- T3 relay DNS fallback must remain limited to credential-free HTTPS session
  probes on `*.t3coderelay.com`, use public addresses, and retain TLS validation.

- For webapp or startup-page launcher changes, follow the catalog, exclusions,
  ordering, and heading-icon rules in [Webapp system](docs/architecture/webapps.md).
  Cover launcher re-renders with DOM regression tests.

- Startup changes must preserve workspace quarantine, default restoration and
  ownership repair, and interactive NBI refresh. Keep boot-time NBI setup free
  of live-server probes. Deferred CVMFS and Slurm run independently after the
  actual Jupyter endpoint answers; cover custom ports and base paths with the
  [startup regression tests](docs/testing.md#startup-performance-regressions).

- T3 environment naming must preserve environment IDs, credentials and custom
  aliases. Learn public hostnames only from configured public URLs or an
  authenticated, XSRF-protected request; never restart an active chat to rename.

- T3 startup error reporting must allowlist safe fields and discard raw child
  output. Cover quota detection through the real subprocess boundary and reset
  stale errors on restart; do not infer a quota from the environment count.

- Embedded T3 chat file links must use Jupyter's default document handler for
  paths inside the server root. Preserve iframe reload/disposal cleanup and
  leave external links, downloads, and modified clicks alone. Cover routing
  with DOM tests that prevent T3's own preview handler from running.
