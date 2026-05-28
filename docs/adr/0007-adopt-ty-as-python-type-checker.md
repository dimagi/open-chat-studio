# ADR-0007: Adopt ty as the Python type checker

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-27</p>

## Context

Open Chat Studio has roughly 1,200 Python files across 36 Django apps and no static type checker configured. Existing type annotations are sparse: 76 files import from `typing` and only 17 use `from __future__ import annotations`. We want lasting safety nets against the kinds of bugs static checking catches (typos in attribute access, broken refactors, drifting return types) without adopting a tool that will be abandoned or become a bottleneck on every CI run.

The Python ecosystem offers several mature options (mypy, pyright/pyre) and one newer entrant — [ty](https://docs.astral.sh/ty/) — from Astral, the team behind `ruff` and `uv` which we already use heavily. ty is in beta, Rust-based, and orders of magnitude faster than mypy on large codebases, but it has a deliberate constraint: [no plans for a plugin system](https://github.com/astral-sh/ty/issues/291), so Django magic (`Manager`, `QuerySet`, model metaclass) cannot be modelled the way `mypy_django_plugin` models it. This matters in practice: without Django-aware stubs a baseline ty run reports ~2,417 `unresolved-attribute` errors (most of them `.objects` manager access), which would drown out any signal from the rules we care about. The canonical stub package `django-stubs` resolves these via a mypy plugin; the maintained plugin-free alternative is [`django-types`](https://pypi.org/project/django-types/), a fork that publishes static hints for the common Django surface.

## Decision

We will adopt ty as the Python type checker for this repository, paired with the `django-types` stubs to make it viable under Django:

1. **Tool.** ty is pinned in `pyproject.toml` (`ty==0.0.38` in `[dependency-groups] dev`), configured under `[tool.ty]` with `python-version = "3.13"` and `python = ".venv"`, and runs in CI via a dedicated `type-check` job in `.github/workflows/lint_and_test.yml` (lines 96–114) on pull requests. Developers also have a documented local command — `uv run ty check apps/` — in `AGENTS.md`.
2. **Django stubs.** We install `django-types` as a dev dependency (in `[dependency-groups] dev`, alongside `ty`) so ty has Django stubs available without requiring plugin support. No additional ty configuration is needed for the stubs to take effect — they ship as a regular package.

## Consequences

- **Positive:** ty runs in seconds on the full codebase, so we can keep it on every PR without budgeting around it. Toolchain alignment with `ruff` and `uv` (all Astral) reduces config surface and version drift.
- **Positive:** The dependency is small and pinned; removing ty later if it does not pan out is a contained change.
- **Positive:** `django-types` installs through the normal `uv sync --dev` flow with no extra tooling step, and dramatically cuts Django-shaped false positives so the rules we enable report mostly real issues.
- **Negative:** ty is in beta. Diagnostic names and exact behavior can change between minor releases, which is why the version is pinned exactly.
- **Negative:** `django-types` is less precise than `django-stubs` for some plugin-driven cases (custom manager resolution, generic `QuerySet[Model]` inference). Some `# ty: ignore[…]` annotations on Django metaprogramming remain unavoidable, and its maintenance velocity is independent from `django-stubs`, so new Django type changes may lag.

## Alternatives considered

- **mypy with `mypy_django_plugin`:** rejected — would fit Django better via the plugin, but is dramatically slower on this codebase and lives outside the Astral toolchain we already standardize on.
- **pyright:** rejected — capable and fast, but Node-based, adding a second runtime to the dev/CI environment, and similarly lacks a Django plugin ecosystem.
- **No static type checker:** rejected — gives up the regression-prevention value we want as the codebase grows, especially around refactors.
- **`django-stubs` (the plugin-backed original):** rejected — requires `mypy_django_plugin`, which has no equivalent under ty by design.
- **No Django stubs at all:** rejected — leaves thousands of `unresolved-attribute` diagnostics on Django patterns, making the noise floor too high to enable meaningful rules.
- **Hand-written local stubs for Django:** rejected — would duplicate work `django-types` already does and become a maintenance burden as Django evolves.
