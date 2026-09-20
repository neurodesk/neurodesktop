# AGENTS.md

## Delivery workflow

1. Keep changes scoped to the request, add appropriate feature tests, and update
   affected documentation. Update AGENTS.md when agent instructions change.
2. Read [testing](docs/testing.md) before changing tests. Run the full unit suite
   and required subsystem checks; test fixes before reporting success. Default
   to `tests/unit/`, using `tests/container/` for checks that need a running image.
   Execute the subject; reserve source assertions for packaging contracts.
3. Commit and push a branch, then open or update its PR with the change summary
   and validation results. Stay with it until review and CI finish.
4. After each push, read all bot review summaries and inline comments. Fix and
   test actionable findings; reply with evidence for inapplicable findings.
   Resolve threads after addressing the feedback.
5. Merge without another confirmation once the latest commit passes all
   applicable tests and CI, bot reviews finish, feedback is addressed, required
   approvals are satisfied, and the PR is mergeable. Respect branch protection;
   pending, cancelled, or missing required checks do not satisfy this gate.
6. Report the PR link, validation results, and merge status. If access or another
   blocker prevents creation or merge, identify it and leave the PR open.

## Documentation

- Follow the [wiki conventions](docs/index.md#conventions). Keep current behavior
  in reference pages and historical records under [docs/designs/](docs/designs/index.md).
- For subsystem changes, read [architecture](docs/architecture.md) and its
  relevant subsystem page. For runtime or build variables, read
  [environment variables](docs/environment-variables.md).
- Preserve `docs/architecture.md`, `docs/testing.md`, and
  `docs/environment-variables.md`; tests and agent workflows reference them.

## Subsystem safeguards

- When changing the root image's base tag or direct package pins, run
  `python scripts/audit_image_versions.py` for the live release report and
  `pytest tests/unit/test_audit_image_versions.py`. The Dockerfile owns current
  versions; keep only package authority and compatibility policy in the audit
  catalog. A complete upgrade still requires a built-image inventory and the
  subsystem checks selected by the changed packages.

- Image workflows must test and scan run-specific candidates before promoting
  architecture, date, or latest tags. Preserve native arm64 runtime coverage,
  pin all checkouts to the run SHA, and install test cleanup before startup.
  See [image release validation](docs/testing.md#image-release-validation).

- Coding-agent CLI upgrades must pass the installed-image T3 and ACP initialization
  probes; check adapter dependency ranges and record any deliberate exception.

- Preserve the package-only sudo default and private VS Code socket. For startup
  privilege or desktop credential changes, follow
  [the security contract](docs/architecture/desktop.md#credentials-and-service-access)
  and run `tests/unit/test_startup_security.py` plus the installed-image checks
  in `tests/container/test_security_policy.py`. Keep root initialization outside
  the notebook user's sudo allowlist. Provider credential injection must validate
  the exact HTTPS authority, including during endpoint changes.

- For webapp or startup-page launcher changes, follow the catalog, exclusions,
  ordering, and heading-icon rules in [Webapp system](docs/architecture/webapps.md).
  Cover launcher re-renders with DOM regression tests.

- Startup changes must preserve workspace quarantine, default restoration and
  ownership repair, and interactive NBI refresh. Keep boot-time NBI setup free
  of live-server probes. Deferred CVMFS and Slurm run independently after the
  actual Jupyter endpoint answers; cover custom ports and base paths with the
  [startup regression tests](docs/testing.md#startup-performance-regressions).

### T3 Code

- T3 Connect changes must preserve Jupyter authentication/XSRF, keep device codes
  transient and OAuth credentials out of browser responses/logs, and distinguish
  a saved link from a reachable tunnel. Cover cancellation, active-chat restart
  protection, expired authorization, and delayed relay routing. T3 provider tests
  must exercise the initialization probe with the image-owned provider path
  for both fresh modern defaults and migration of the exact legacy default.

- T3 relay DNS fallback must remain limited to credential-free HTTPS session
  probes on `*.t3coderelay.com`, use public addresses, and retain TLS validation.

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
