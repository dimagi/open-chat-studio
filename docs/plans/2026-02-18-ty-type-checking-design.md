# Incremental ty Type Checking Adoption

**Date:** 2026-02-18
**Status:** Approved

## Goal

Gradually adopt [ty](https://docs.astral.sh/ty/) (Astral's Rust-based Python type checker) across the
Open Chat Studio codebase, working toward full type coverage over time.

## Context

- 1,209 Python files across 36 Django apps
- No existing type checker configured
- Minimal existing type annotations (76 files import from `typing`, 17 use `from __future__ import annotations`)
- No `django-stubs` or `django-types` installed

### Baseline ty diagnostics (v0.0.17, all defaults)

| Rule | Count | Notes |
|------|-------|-------|
| `unresolved-attribute` | 2,417 | Mostly Django `.objects` manager |
| `invalid-argument-type` | 369 | Mix of real issues and Django false positives |
| `invalid-assignment` | 109 | |
| `not-subscriptable` | 107 | |
| `invalid-parameter-default` | 79 | |
| `invalid-method-override` | 46 | Django metaclass patterns |
| `possibly-missing-attribute` | 43 | |
| `invalid-return-type` | 33 | |
| `unsupported-operator` | 31 | |
| `invalid-type-form` | 29 | |
| Other rules | < 15 each | |
| **Total** | **3,332** | |

### Key constraint

ty is in beta and does not support Django plugins ([no plans to add a plugin system](https://github.com/astral-sh/ty/issues/291)).
The `django-types` package (plugin-free stubs forked from `django-stubs`) is the recommended workaround.

## Design

### Strategy: Hybrid rule tiers + progressive enablement

Start with all rules ignored, install `django-types` to reduce false positives, then enable rules
in tiers from lowest-noise to highest-noise. Each tier is a series of small PRs.

### Phase 0: Foundation (1 PR)

- Install `django-types` as a dev dependency
- Add `ty` as a dev dependency
- Create `[tool.ty]` config in `pyproject.toml`:
  - Set `all = "ignore"` (start from zero)
  - Exclude `migrations/` directory
- Add non-blocking CI step to `lint_and_test.yml` (`continue-on-error: true`)
- Add `ty check` to AGENTS.md useful commands
- Re-measure baseline with `django-types` installed

### Phase 1: High-value, low-noise rules (small PRs)

Enable rules that catch real bugs with few false positives. Each rule or small group = 1 PR.

- `unresolved-import` (5 errors)
- `unresolved-reference` / `unresolved-global`
- `invalid-raise`
- `call-non-callable`
- `missing-argument` / `too-many-positional-arguments`
- `duplicate-base`, `cyclic-class-definition`
- Other rules with 0-5 violations

### Phase 2: Medium-noise rules (larger PRs, may need `# ty: ignore`)

- `invalid-return-type` (~33 errors)
- `invalid-assignment` (~109 errors)
- `unsupported-operator` (~31 errors)
- `invalid-parameter-default` (~79 errors)
- `not-subscriptable` (~107 errors, likely many from Django/third-party)

Some violations will require `# ty: ignore[rule]` inline comments for legitimate Django patterns.

### Phase 3: High-noise rules

- `invalid-argument-type` (~369 errors, many Django false positives)
- `unresolved-attribute` (~2,417 errors, depends on `django-types` reduction)
- `invalid-method-override` (~46 errors, Django metaclass patterns)

### Phase 4: Make CI blocking

- Remove `continue-on-error: true` from the ty CI step
- ty violations now block PRs

## Suppression strategy

- Prefer fixing real issues over suppressing
- Use `# ty: ignore[rule-name]` for known false positives (Django metaprogramming)
- Use `@no_type_check` sparingly for functions that are fundamentally untyped
- Configure rule severity in `[tool.ty.rules]` — never use blanket `# type: ignore`

## CI integration

Add a new job to `.github/workflows/lint_and_test.yml`:

```yaml
type-check:
  if: github.ref != 'refs/heads/main'
  runs-on: ubuntu-latest
  continue-on-error: true
  steps:
    - uses: actions/checkout@v6
    - uses: astral-sh/setup-uv@v7
    - name: Install dependencies
      run: |
        uv venv
        uv sync --locked --dev
    - name: Run ty
      run: uv run ty check apps/
```

## Sources

- [ty documentation](https://docs.astral.sh/ty/)
- [ty GitHub repo](https://github.com/astral-sh/ty)
- [ty rules reference](https://docs.astral.sh/ty/reference/rules/)
- [Django plugin support discussion](https://github.com/astral-sh/ty/issues/291)
- [django-types on PyPI](https://pypi.org/project/django-types/)
