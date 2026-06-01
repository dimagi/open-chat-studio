# ADR-0027: Secrets exclusion via per-resource allowlist serializers

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>

## Context

The inspect endpoint is read by an external agent and aggregates resources drawn from many apps. A single leaked field is a credential breach. The resource models hold sensitive material: encrypted provider `config` blobs (API keys, bot tokens, OAuth credentials), signed file-storage URLs, and freeform channel authorization blobs (`extra_data`).

## Decision

We will serialize each resource through its own serializer with an **explicit allowlist** of fields — never `__all__` and never a denylist. Concretely: encrypted provider `config`, signed file-storage URLs, and channel `extra_data` are excluded outright; a custom action's auth provider is surfaced as name and type only. Adding a field to a model never exposes it by default. A test asserts that excluded keys (such as `config`) appear nowhere in the response payload. (Non-secret fields may still be trimmed for size or relevance — e.g. a custom action's OpenAPI schema is reduced to a path/operation digest — but that is a payload-minimization choice, not a secrets measure; OpenAPI schemas are public by design.)

## Consequences

- Exposure is opt-in and safe by default: a newly added model field is invisible until someone deliberately allowlists it.
- A serializer per resource type plus a deliberate field-by-field audit must be written and maintained.

## Alternatives considered

- `__all__` with a denylist of sensitive fields — rejected: a newly added sensitive field leaks until someone remembers to add it to the denylist. Channel `extra_data` is the clearest trap — it is a freeform blob that is mostly authorization material — so it is dropped wholesale rather than stripped.
