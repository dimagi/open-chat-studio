# ADR-0024: Defer stable public IDs to a future write API

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>
<p class="adr-meta">Extends: <a href="0023-inline-nested-resource-tree.md">ADR-0023</a></p>

## Context

An earlier plan added a `public_id` UUID to the ~21 resource models the v2 API exposes, so nested *references* in the inspect payload could be dereferenced by a stable, environment-portable key (tracked in a separate prerequisite issue). The switch to an inline nested tree ([ADR-0023](0023-inline-nested-resource-tree.md)) removed that need: the consumer never addresses a resource by id during a read. What remained was speculative forward-compatibility with a future write API.

A non-guessable identifier carries a security benefit only where a resource is reachable without authentication. `Experiment`, `Participant`, and `ExperimentSession` have UUID/opaque identifiers precisely because they appear in public, unauthenticated URLs (the web widget, the OpenAI-compatible endpoint), where a sequential integer would invite enumeration. The resources the inspect payload embeds are reachable only through the authenticated, team-scoped endpoint ([ADR-0026](0026-inspect-authorization-team-scoped.md)), so a caller can already see only its own team's resources — a non-guessable id adds little there. That removes the main remaining argument for the migration.

## Decision

We will not add `public_id` fields to the v2-exposed resource models. The chatbot keeps its existing `Experiment.public_id` (already its lookup field); embedded resources carry their numeric database primary key as `id`. The public-ID migration is dropped as a prerequisite. If and when a write API is specified, it can introduce stable external IDs scoped to the specific resources it accepts as references.

## Consequences

- The inspect endpoint ships without a large migration — no shared mixin, no backfills, no per-version UUID reset in versioning logic.
- The read payload exposes numeric database primary keys for nested resources. This is acceptable because the endpoint is authenticated and team-scoped ([ADR-0026](0026-inspect-authorization-team-scoped.md)): unlike the public-facing chatbot identifier, these ids are never exposed unauthenticated, so their guessability is not an enumeration risk. They are, however, not portable across environments.
- A later write API inherits the obligation to add stable IDs where it needs them, rather than the read API carrying them speculatively.

## Alternatives considered

- Add `public_id` to all v2-exposed models up front — rejected: the inline tree removed the dereferencing rationale, leaving only speculative forward-compat (YAGNI).
- Scope public IDs to only the write-addressable resources now — rejected: still a sizable migration for a write API that has no specification yet, and it mixes id types across the payload.
