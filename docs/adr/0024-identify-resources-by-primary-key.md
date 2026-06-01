# ADR-0024: Identify v2-exposed resources by database primary key

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>
<p class="adr-meta">Extends: <a href="0026-inspect-authorization-team-scoped.md">ADR-0026</a></p>

## Context

The v2 API exposes ~21 resource models. The question is whether each should carry a non-guessable UUID public ID, or whether its numeric database primary key is sufficient as the identifier in the payload.

The only thing a UUID adds over the primary key is non-guessability, which matters solely where a resource is reachable without authentication. `Experiment`, `Participant`, and `ExperimentSession` carry opaque identifiers precisely because they appear in the public, unauthenticated web widget, where a sequential integer would invite enumeration. The inspect payload's embedded resources are reachable only through the authenticated, team-scoped endpoint ([ADR-0026](0026-inspect-authorization-team-scoped.md)), so a caller already sees only its own team's resources.

## Decision

We will identify v2-exposed resources by their numeric database primary key as `id`, and add no UUID `public_id` fields. The chatbot keeps its existing public-facing `Experiment.public_id` (already its lookup field).

## Consequences

- The read payload exposes numeric primary keys for nested resources. Guessability is not an enumeration risk here, because the endpoint is authenticated and team-scoped ([ADR-0026](0026-inspect-authorization-team-scoped.md)).

## Alternatives considered

- Add `public_id` UUIDs to all v2-exposed models — rejected: a UUID's only advantage over the primary key is non-guessability, which an authenticated, team-scoped endpoint does not need.
- Scope UUIDs to only the write-addressable resources now — rejected: still a sizable migration for an unspecified write API, and it mixes id types across the payload.
