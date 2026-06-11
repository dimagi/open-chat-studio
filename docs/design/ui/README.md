# Unified Assessment — UI design briefs

Reference documents for designing the UI of the unified assessment system. Each brief targets one cluster of screens and is intended to be self-contained enough that a designer can work from it directly, while remaining anchored to the canonical data-model and decision-record in [`../unified-assessment.md`](../unified-assessment.md).

## Status

- **Score table** — shipped. See [`apps/assessments/models.py`](../../../apps/assessments/models.py).
- **Concordance v0** — live for testing behind `flag_assessments_concordance`. Eval ↔ queue picker, categorical agreement % on shared field. See [`apps/assessments/views.py`](../../../apps/assessments/views.py) and [`templates/assessments/concordance.html`](../../../templates/assessments/concordance.html).
- **Everything else in these briefs** — not built yet. The pre-unified UIs (eval configs, evaluators, annotation queues, annotate flow) are what the unified screens replace.

## Briefs

| # | Brief | Status |
|---|---|---|
| 01 | [Config: Assessment + Schema + Scorers](./01-config-assessment-schema-scorers.md) | draft |
| 02 | [Source + Routing rules](./02-source-and-routing-rules.md) | draft |
| 03 | [Review workflow (HumanScorer)](./03-review-workflow.md) | draft |
| 04 | [Analytics: Runs + Trends + Concordance v1](./04-analytics-runs-trends-concordance.md) | draft |

## How to read a brief

Each brief follows the same shape:

1. **Purpose & scope** — what cluster of screens this covers, what it does *not*.
2. **User stories addressed** — pointer back to the stories in [`unified-assessment.md`](../unified-assessment.md#user-stories) that drive these screens.
3. **Information architecture** — the list of screens and how they nest under the Assessment.
4. **Per-screen specs** — for each screen: purpose, primary user, information shown, primary actions, key states (empty / loading / error / populated), and the components / data references that drive layout decisions.
5. **Cross-cutting concerns** — permissions, feature-flag gating, empty-team experience, archiving.
6. **Open design questions** — explicitly-unresolved points that a designer should flag rather than guess.

## Conventions

- **DaisyUI + Tailwind** is the project's component library. Existing patterns to reuse (verified in the concordance template): `breadcrumbs`, `stats` / `stat`, `badge` (`badge-success`, `badge-error`, `badge-ghost`, `badge-warning`), `alert` (`alert-info`, `alert-warning`), `form-control` + `label-text`, `select select-bordered`, `btn` (`btn-primary`, `btn-xs`, `join` / `join-item`), `table`, `card`, `tabs` / `tab`, `drawer`, `modal`.
- **HTMX + Alpine.js** for in-page interactions over Django templates. React/TypeScript for islands of richer interaction (the pipeline builder is the existing reference). Don't introduce a new SPA layer for these flows unless a brief explicitly calls for it.
- **Icons** are FontAwesome 6 (`fa-solid`, `fa-fw`) — see the concordance template for examples.
- **Generic snippets** to reuse: `generic/chip_button.html` (compact link chip — used in concordance for session links).
- **Feature flag**: the unified assessment surface lives behind `ASSESSMENTS` (per FR-10.6), replacing today's separate `flag_evaluations` and `flag_human_annotations`. Briefs assume the flag is active.

## Canonical references

When in doubt, the canonical source is [`docs/design/unified-assessment.md`](../unified-assessment.md). Briefs link to specific decision records (D-1 through D-16) when a UI choice is driven by a back-end constraint.
