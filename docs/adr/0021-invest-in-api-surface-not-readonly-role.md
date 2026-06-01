# ADR-0021: Invest in API surface, not a read-only role

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>

## Context

There is a use case for agent-based workflows to inspect a chatbot's configuration without risking accidental mutation. The original framing was a read-only *user role* that could see every view but change nothing, implemented by checking `change_*` permissions on writes — a broad change to the permission model.

OCS already ships a narrower mechanism: a `UserAPIKey.read_only` flag, enforced by `ReadOnlyAPIKeyPermission`, which blocks unsafe HTTP methods for read-only keys. The real gap is not enforcement — it is that the API does not expose enough of a chatbot's configuration to inspect it.

## Decision

We will not build a read-only role. We will reuse the existing `read_only` API-key mechanism and invest the effort in expanding the read API surface — specifically a deep chatbot-inspection endpoint. Read-only agent access is treated as an API-key concern, not a role concern.

## Consequences

- No change to the permission/role model; an operator can issue a `read_only` key today and pair it with the new endpoint.
- All the effort (and ongoing maintenance) shifts onto the inspection surface and its secret-exclusion guarantees.
- Sets the precedent that "let an agent look but not touch" is solved with a scoped key plus a read endpoint, not a new role.

## Alternatives considered

- A read-only role checking `change_*` permissions on POST/PATCH/DELETE — rejected as disproportionate effort for a need already met by `read_only` API keys.
