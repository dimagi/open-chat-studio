# ADR-0007: Adopt ty as the Python type checker

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-27</p>

## Context

Open Chat Studio is a large Django codebase with no static type checker and sparse type annotations. We want the regression-prevention value of static checking (attribute typos, broken refactors, drifting return types) without adopting a tool that becomes a CI bottleneck or gets abandoned.

[ty](https://docs.astral.sh/ty/) is a Rust-based, beta type checker from Astral (the makers of `ruff` and `uv`, which we already use), much faster than mypy on large codebases. It [has no plugin system by design](https://github.com/astral-sh/ty/issues/291), so it cannot model Django's `Manager`/`QuerySet` metaprogramming the way `mypy_django_plugin` does; without Django-aware stubs a baseline run reports thousands of `unresolved-attribute` errors that drown out real signal. [`django-types`](https://pypi.org/project/django-types/) is the maintained, plugin-free stub package that resolves most of this.

## Decision

We will adopt ty as the Python type checker, paired with `django-types` stubs:

1. **Tool.** Pin `ty` in `[dependency-groups] dev` of `pyproject.toml`, configured under `[tool.ty]` with `python-version = "3.13"` and `python = ".venv"`. Run it in CI as a `type-check` job in `.github/workflows/lint_and_test.yml` on pull requests, with `uv run ty check apps/` documented as the local command in `AGENTS.md`.
2. **Django stubs.** Install `django-types` as a dev dependency alongside `ty`. It ships as a regular package, so no extra ty configuration is needed.

## Consequences

- **Positive:** ty runs in seconds, so it stays on every PR without CI budgeting.
- **Positive:** Toolchain alignment with `ruff` and `uv` (all Astral) reduces config surface and version drift.
- **Positive:** The dependency is small and pinned, so removing ty later is a contained change.
- **Positive:** `django-types` installs via the normal `uv sync --dev` flow and cuts most Django-shaped false positives.
- **Negative:** ty is in beta, so diagnostic names and behavior can shift between releases — hence the exact pin.
- **Negative:** `django-types` is less precise than `django-stubs` for plugin-driven cases (custom manager resolution, generic `QuerySet[Model]` inference), so some `# ty: ignore[…]` annotations remain, and its maintenance velocity may lag Django changes.

## Alternatives considered

- **mypy with `mypy_django_plugin`** → rejected: fits Django via its plugin but is far slower and outside the Astral toolchain.
- **pyright** → rejected: fast and capable but Node-based, adding a second runtime, and also lacks a Django plugin.
- **No static type checker** → rejected: gives up regression prevention as the codebase grows.
- **`django-stubs`** → rejected: requires `mypy_django_plugin`, which has no ty equivalent by design.
- **No Django stubs** → rejected: leaves thousands of `unresolved-attribute` diagnostics, raising the noise floor too high to enable meaningful rules.
- **Hand-written local Django stubs** → rejected: duplicates `django-types` and becomes a maintenance burden as Django evolves.
