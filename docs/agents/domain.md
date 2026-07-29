# Domain documentation

This is a single-context repository. Engineering skills should use the
repository's established vocabulary and architectural decisions when they
explore or change the codebase.

## Before exploring

Read these resources when they exist and are relevant:

- `CONTEXT.md` at the repository root for domain language and boundaries;
- `docs/adr/` for repository-wide architectural decisions;
- the focused architecture, testing, and environment documentation linked
  from `AGENTS.md`.

If `CONTEXT.md` or `docs/adr/` does not exist yet, proceed silently. Those
artifacts can be introduced later when a domain term or architectural decision
needs to be recorded.

## Consumer rules

- Use the terms defined in `CONTEXT.md` in issue titles, hypotheses, tests, and
  implementation plans.
- Do not substitute synonyms that the glossary explicitly avoids.
- Surface any conflict with an existing ADR instead of silently overriding it.
- Keep new ADRs repository-wide unless the repository is deliberately changed
  to a documented multi-context layout.
