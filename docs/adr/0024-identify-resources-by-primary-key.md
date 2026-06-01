# ADR-0024: Identify v2-exposed resources by database primary key

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>
<p class="adr-meta">Extends: <a href="0023-inline-nested-resource-tree.md">ADR-0023</a></p>

## Context

The v2 API exposes ~21 resource models. The question is whether each should carry a non-guessable UUID public ID, or whether its numeric database primary key is sufficient as the identifier in the payload.

A UUID would serve one of two purposes. The first is stable, environment-portable dereferencing of nested references — but the inline nested tree ([ADR-0023](0023-inline-nested-resource-tree.md)) conveys wiring by containment, so the consumer never addresses a resource by id during a read. The second is enumeration protection, which only helps where a resource is reachable without authentication. `Experiment`, `Participant`, and `ExperimentSession` carry opaque identifiers precisely because they appear in public, unauthenticated URLs (the web widget, the OpenAI-compatible endpoint), where a sequential integer would invite enumeration. The inspect payload's embedded resources are reachable only through the authenticated, team-scoped endpoint ([ADR-0026](0026-inspect-authorization-team-scoped.md)), so a caller already sees only its own team's resources.

Neither purpose applies. What remains is speculative forward-compatibility with a future write API.

## Decision

We will identify v2-exposed resources by their numeric database primary key as `id`, and add no UUID `public_id` fields. The chatbot keeps its existing public-facing `Experiment.public_id` (already its lookup field). If and when a write API is specified, it can introduce stable external IDs scoped to the resources it accepts as references.

## Consequences

- The inspect endpoint ships without a migration — no shared mixin, no backfills, no per-version UUID reset in versioning logic.
- The read payload exposes numeric primary keys for nested resources. Guessability is not an enumeration risk here, because the endpoint is authenticated and team-scoped ([ADR-0026](0026-inspect-authorization-team-scoped.md)). These ids are, however, not portable across environments.
- A future write API inherits the obligation to add stable IDs where it needs them.

## Alternatives considered

- Add `public_id` UUIDs to all v2-exposed models — rejected: neither dereferencing nor enumeration protection applies, leaving only speculative write-API forward-compat (YAGNI).
- Scope UUIDs to only the write-addressable resources now — rejected: still a sizable migration for an unspecified write API, and it mixes id types across the payload.
