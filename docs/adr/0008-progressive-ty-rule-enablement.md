# ADR-0008: Enable ty rules progressively from a baseline of all-ignored

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-27</p>
<p class="adr-meta">Extends: <a href="0007-adopt-ty-as-python-type-checker.md">ADR-0007</a></p>

## Context

A clean ty run reports ~3,332 diagnostics, dominated by `unresolved-attribute` (~2,417), `invalid-argument-type` (~369), and `invalid-assignment` (~109), even after the `django-types` stubs ([ADR-0007](0007-adopt-ty-as-python-type-checker.md)) cut the Django noise. Turning ty on with defaults and blocking CI would either freeze development behind a multi-week cleanup or push contributors toward blanket suppressions.

The distribution is uneven: many rules have zero or a handful of violations (high-signal, cheap to lock in) while a few account for most of the noise. We want regression prevention now, plus a path to grow coverage without big-bang merges.

## Decision

We will baseline ty with `all = "ignore"` in `[tool.ty.rules]`, then promote individual rules to `"error"` one at a time, prioritising rules with zero or very few violations.

- The CI `type-check` job runs with `continue-on-error: true` so violations surface as warnings, not merge blockers; the job is gated to non-`main` branches (`if: github.ref != 'refs/heads/main'`).
- Generated and out-of-scope trees (`migrations/`, `locust/`, `docs/plans/`) are excluded under `[tool.ty.src]`.
- Unavoidable suppressions use scoped `# ty: ignore[rule-name]`, never blanket `# type: ignore`.
- A future change flips CI to blocking by removing `continue-on-error`.

## Consequences

- **Positive:** Each rule enablement is a small, reviewable PR.
- **Positive:** A rule gains regression prevention the moment it moves to `"error"`.
- **Positive:** Contributors are never blocked by pre-existing type debt.
- **Positive:** The per-rule list in `[tool.ty.rules]` documents which rules we trust today.
- **Negative:** The rule list grows long and must track ty's evolving catalogue across beta releases.
- **Negative:** While CI is non-blocking, regressions on disabled rules can land without immediate signal.

## Alternatives considered

- **Enable all rules and block CI from day one** → rejected; requires fixing or suppressing 3,000+ diagnostics before anything else can merge.
- **Enable all rules with a blanket repo-wide `# ty: ignore` sweep** → rejected; produces noise without value and trains reflexive suppression.
- **Per-app or per-directory rollout instead of per-rule** → rejected; a real bug in an unreached rule is just as bad in a "covered" app, and per-rule matches how diagnostics distribute.
- **Keep ty out of CI entirely, run locally only** → rejected; loses the continuous regression prevention that motivated adopting ty.
