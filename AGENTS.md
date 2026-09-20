# AGENTS.md

## Agent Guidelines

- Keep changes scoped to the requested work and avoid unrelated refactors.
- When fixing a bug, test the fix before reporting that the problem is fixed.
- When implementing a new feature, add appropriate tests under tests/ and keep the docs/ and AGENTS.md up to date.
- Tests live in two tiers: `tests/unit/` runs on a checkout with `pytest
  tests/unit` and is the default home for new tests; `tests/container/` runs
  inside the built image with `pytest /opt/tests/` and is only for assertions
  that need a running container. Only `tests/container/` is copied into the
  image. Keep `/opt/tests` readable by the unprivileged `jovyan` test user even
  when the checkout has a restrictive umask. Resolve a test's subject through
  the helpers in `tests/testlib.py`.
- Test behavior by executing the subject and checking its result. Use source
  assertions for packaging contracts; use valid inputs and verified prerequisites
  for negative tests. Required services and directories must fail when absent,
  and skip only when the selected profile explicitly disables them. See
  [testing](docs/testing.md) for workflow fixtures and runtime checks.
- The docs are a hierarchical wiki rooted at [`docs/index.md`](docs/index.md):
  every page carries YAML frontmatter (`title`, `description`, `parent`,
  `status`, `last-reviewed`) and cross-references relatives with markdown
  links. Historical assessments, plans, and audits live under
  [`docs/designs/`](docs/designs/index.md) as records; keep current behavior
  in the reference pages, not in the records.
- When changing the root image's base tag or direct package pins, run
  `python scripts/audit_image_versions.py` for the live release report and
  `pytest tests/unit/test_audit_image_versions.py`. The Dockerfile owns current
  versions; keep only package authority and compatibility policy in the audit
  catalog. A complete upgrade still requires a built-image inventory and the
  subsystem checks selected by the changed packages.
- Use [`docs/architecture.md`](docs/architecture.md) for project architecture,
  startup flow, and directory layout; it links to one page per subsystem
  under [`docs/architecture/`](docs/architecture/), including
  [build-time behavior](docs/architecture/build.md).
- Use [`docs/environment-variables.md`](docs/environment-variables.md) for
  supported runtime and build environment variables.
- `docs/architecture.md`, `docs/testing.md`, and
  `docs/environment-variables.md` are referenced by path from tests and the
  agent workflows; do not move or rename them.

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

- Keep container-backed apps in the Jupyter startup page Webapps section. Use
  one More webapps link to `https://webapps.neurodesk.org/` for external apps;
  do not restore individual legacy hosted-app links. Exclude dicompare and
  QSMbly from local launchers, since they are maintained externally.

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
