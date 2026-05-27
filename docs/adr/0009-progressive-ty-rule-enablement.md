# ADR-0009: Enable ty rules progressively from a baseline of all-ignored

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-27</p>
<p class="adr-meta">Extends: <a href="0007-adopt-ty-as-python-type-checker.md">ADR-0007</a></p>

## Context

A clean ty run on the codebase reports ~3,332 diagnostics across many rules — including ~2,417 `unresolved-attribute`, ~369 `invalid-argument-type`, and ~109 `invalid-assignment` — even after [ADR-0008](0008-django-types-stubs-for-ty.md) cuts the Django noise. Turning ty on with defaults and blocking CI would either freeze development behind a multi-week cleanup or push every contributor toward blanket suppressions that defeat the point of running a type checker.

The diagnostic distribution is uneven: many rules report zero or a handful of violations (high-signal, low-effort to fix and lock in) while a few rules account for most of the noise (high-effort, mixed signal). We want regression prevention now — without a multi-week cleanup as a prerequisite — and a path that increases coverage over time without big-bang merges.

## Decision

We will configure ty with `all = "ignore"` as the baseline in `[tool.ty.rules]` (see `pyproject.toml`), then enable individual rules to `"error"` one at a time, prioritising rules with zero or very few existing violations first. The CI `type-check` job in `.github/workflows/lint_and_test.yml` runs with `continue-on-error: true` (line 98) during this rollout so violations surface as warnings rather than blocking merges; the job is also gated to non-`main` branches (`if: github.ref != 'refs/heads/main'`). We additionally exclude generated and out-of-scope trees (`migrations/`, `locust/`, `docs/plans/`) under `[tool.ty.src]`. Suppressions, when unavoidable, use scoped `# ty: ignore[rule-name]` comments — never the blanket `# type: ignore`. Once enough rules are enabled and stable, a future change will flip CI to blocking by removing `continue-on-error`.

## Consequences

- **Positive:** Each rule enablement is a small, reviewable PR. The repo gains regression prevention for every rule with zero current violations the moment that rule moves to `"error"` — currently dozens of rules, listed individually in `[tool.ty.rules]`.
- **Positive:** Contributors are never blocked by pre-existing type debt; the type-checker reports warnings until the project is ready for it to block.
- **Positive:** The explicit per-rule list in `pyproject.toml` doubles as documentation of which rules we trust today.
- **Negative:** The rule list grows long and must be maintained as ty's rule catalogue evolves between beta releases. Renaming or removing a rule upstream means a config update.
- **Negative:** While CI is non-blocking, regressions on disabled rules can land without immediate signal; this is an accepted trade-off until enablement matures.

## Alternatives considered

- **Enable all rules and block CI from day one:** rejected — would require fixing or suppressing 3,000+ diagnostics before any other work could merge.
- **Enable all rules with a blanket repo-wide `# ty: ignore` sweep:** rejected — produces noise without value and trains contributors to add suppressions reflexively.
- **Per-app or per-directory rollout instead of per-rule:** rejected — a real bug in a rule we haven't reached yet is just as bad in a "covered" app as anywhere else; the per-rule axis matches how diagnostics distribute in the codebase.
- **Keep ty out of CI entirely and run it locally only:** rejected — without continuous feedback we lose the regression prevention that motivated adopting ty in the first place.
