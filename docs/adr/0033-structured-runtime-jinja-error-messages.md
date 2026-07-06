# ADR-0033: Structured runtime Jinja error messages

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-04</p>

## Context

When a Jinja template in the Email or Template node failed at run time, the trace recorded a generic `Error rendering template: <exception>`. That message named no field, gave no error category, and — for the common case of a misspelled variable — did not say which variables were actually available. Undefined-variable errors cannot be caught at edit or save time (see ADR-0032), so the run-time message is the user's only feedback for them.

## Decision

We will format every Jinja exception through a shared helper that categorises it and produces an actionable, field-scoped message before raising `PipelineNodeRunError`:

- **UndefinedError** — names the field and appends the available top-level variable names (when the render context is in scope).
- **TemplateSyntaxError** — names the field and includes the line number when present.
- **SecurityError** (sandbox escape) — names the field and the unsafe access.
- **Any other exception** — names the field and includes the exception type and message.

The field name matches the Pydantic field (e.g. `template_string`, `subject`, `recipient_list`, `body`). The available-variables list is omitted when the failure occurs during the parse phase, before the context dict is built.

## Consequences

- Trace errors are self-describing: a user reading a trace sees the field, the error class, and (for undefined variables) the valid variable names.
- The `Trace` model is unchanged — the richer text is stored in the existing error field, so no migration or schema change.
- The helper must be called explicitly in each node's except blocks; a new node rendering Jinja must opt in to get the structured message.

## Alternatives considered

- **Keep the generic `Error rendering template: {e}` string** — rejected; it omits the field, the category, and the available variables, which are the three things a user needs to fix the template.
- **Add an error-detail column to the `Trace` model** — rejected; the structured message fits the existing error field, and a schema change would cost a migration for no added capability.
