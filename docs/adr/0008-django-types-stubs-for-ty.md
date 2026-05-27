# ADR-0008: Use django-types stubs to support Django typing under ty

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-27</p>
<p class="adr-meta">Extends: <a href="0007-adopt-ty-as-python-type-checker.md">ADR-0007</a></p>

## Context

Following [ADR-0007](0007-adopt-ty-as-python-type-checker.md), we adopted ty as our Python type checker. Django relies heavily on metaclass and descriptor magic — `Model.objects` is injected by the metaclass, `QuerySet` returns are parameterised by model type, and form/serializer fields use descriptors — none of which a plain static checker can resolve without help. Without Django-aware stubs, a baseline ty run reports ~2,417 `unresolved-attribute` errors (most of them `.objects` manager access), which would drown out any signal from rules we actually care about.

The canonical Django stub package is `django-stubs`, but it ships behind a mypy plugin to resolve dynamic attributes correctly. ty has explicitly stated it [will not add a plugin system](https://github.com/astral-sh/ty/issues/291). The maintained workaround is [`django-types`](https://pypi.org/project/django-types/), a plugin-free fork of `django-stubs` that publishes static type hints for the common Django surface.

## Decision

We will install `django-types` as a dev dependency (declared in `pyproject.toml` under `[dependency-groups] dev`, alongside `ty`) so ty has Django stubs available without requiring plugin support. No additional ty configuration is needed for the stubs to take effect — they ship as a regular package.

## Consequences

- **Positive:** Dramatically fewer Django-shaped false positives, so the rules we enable in [ADR-0009](0009-progressive-ty-rule-enablement.md) report mostly real issues.
- **Positive:** Stubs install through the normal `uv sync --dev` flow; no extra tooling step.
- **Negative:** `django-types` is less precise than `django-stubs` for some plugin-driven cases (custom manager resolution, generic `QuerySet[Model]` inference). Some `# ty: ignore[…]` annotations on Django metaprogramming remain unavoidable.
- **Negative:** Maintenance velocity of `django-types` is independent from `django-stubs`; if upstream Django introduces new type-affecting changes, support may lag.

## Alternatives considered

- **`django-stubs` (the plugin-backed original):** rejected — requires `mypy_django_plugin`, which has no equivalent under ty by design.
- **No stubs at all:** rejected — leaves thousands of `unresolved-attribute` diagnostics on Django patterns, making the noise floor too high to enable meaningful rules.
- **Hand-written local stubs for Django:** rejected — would duplicate work `django-types` already does and become a maintenance burden as Django evolves.
