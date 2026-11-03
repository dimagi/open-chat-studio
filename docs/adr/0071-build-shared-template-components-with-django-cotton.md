# ADR-0071: Build shared template components with django-cotton

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-11-03</p>

## Context

The UI is server-rendered Django templates with daisyUI classes, HTMX and Alpine.js; React is mounted only for the pipeline editor. Reusable markup is shared three ways: `{% include ... with %}` fragments under `templates/generic/`, Python template tags for form fields (`render_field`), and `{% partialdef %}` from django-template-partials for in-page fragments. Includes cannot wrap content, so `generic/help.html` takes pre-rendered HTML; they cannot pass arbitrary attributes through, so `hx-*` and `x-*` cannot reach a fragment's root element; and their parameters are undocumented unless a comment header exists. Most daisyUI patterns (over a hundred each of `btn btn-primary` and `badge`) are written inline, so a design standardisation pass has to edit every occurrence and nothing prevents drift afterwards.

A designer is producing standard layouts and components as HTML-and-class patterns. We need a place for those patterns that a template author can call by name.

## Decision

We will build shared UI components as [django-cotton](https://django-cotton.com/) components under `templates/cotton/`, called from templates as HTML tags (`<c-ui.chip :url="obj.url" :label="obj.name" />`).

- `ui/` holds presentation primitives owned by the design work; `app/` holds domain pieces composed from them.
- Every component declares its parameters with `<c-vars>` and puts `{{ attrs }}` on its root element so callers can pass `hx-*`, `x-*`, `id` and extra classes.
- Once a `ui/` primitive exists, new templates use it instead of writing the daisyUI classes by hand. Existing `{% include "generic/..." %}` call sites keep working through thin wrappers until they are migrated.
- `{% partialdef %}` stays for repeating a fragment within one page and for HTMX fragment responses.
- Both django-cotton and django-template-partials are installed through their `SimpleAppConfig` and the template loader chain is spelled out in `config/settings.py`, since each library's default AppConfig overwrites the other's loader wrapping.
- A staff-only UI Gallery page under the global admin renders every component and pattern for review in both themes.

## Consequences

- **Positive:** A pattern is defined once and changed once; call sites read as `<c-ui.button variant="primary">`.
- **Positive:** Components are plain templates compiled to native template tags, so there is no runtime cost and no Python class per component.
- **Positive:** Tooling already fits: djlint formats `<c-*>` tags, Tailwind scans `templates/cotton/`, a VS Code plugin provides completion and missing-component diagnostics.
- **Negative:** One more template dialect to learn; the main trap is that a single colon means a Django expression, so Alpine's `:bind` shorthand is written `::bind` inside a component tag.
- **Negative:** Two reuse mechanisms coexist until the `generic/` includes are migrated.
- **Negative:** The loader chain is now explicit in settings and must be kept in step when either library changes its loader.

## Alternatives considered

- **django-components** → rejected: its strengths (Python class per component, per-component JS/CSS bundles, scoped slots, typed inputs) serve behaviour we keep in Alpine/HTMX and CSS we keep in one Tailwind bundle; pre-1.0 API churn and a self-reported 4x render slowdown are costs without a matching benefit here.
- **django-bird** → rejected: same model as cotton with a `{% bird %}` syntax, tiny adoption, no editor or formatter support.
- **Django 6 partials only** → rejected: no slots, no attribute passthrough, no cross-file reuse without `template#partial` indirection; they complement components rather than replace them.
- **Keep `{% include %}` fragments and add conventions** → rejected: conventions cannot add slots or attribute passthrough, which are the concrete gaps.
- **React for page chrome** → rejected: splits every page between two rendering models for server-rendered HTMX pages.
