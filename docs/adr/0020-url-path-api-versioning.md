# ADR-0020: URL-path API versioning, v1 frozen / v2 new

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>

## Context

The public API has no versioning. We need to rename the external surface (see [ADR-0021](0021-rename-experiment-to-chatbot-in-v2.md)) and add new endpoints, but doing either in place would break existing callers — and request logs confirm the current `/api/experiments/` surface has real external consumers. API shapes ossify once consumed, so this is the moment to introduce a version boundary.

## Decision

We will adopt DRF URL-path versioning with `ALLOWED_VERSIONS = ["v1", "v2"]`. `/api/v1/` exposes today's surface, frozen — no new features — and the existing unversioned `/api/experiments/…` routes remain a permanent alias of v1 so current callers never break. All new endpoints and the renamed surface land under `/api/v2/`. Each version gets its own OpenAPI schema and docs. No deprecation timer on v1 yet; a `Sunset`/`Deprecation` signal can be added once external adoption of v2 is understood.

## Consequences

- The version is explicit in every URL and log line, and trivially testable from curl or a script.
- v1 and v2 can share zero code, so v2 is free to diverge.
- Two routers, two schemas, and two docs surfaces must be maintained indefinitely.

## Alternatives considered

- Header-based versioning (`Accept: application/vnd.ocs.v2+json`) — rejected: easy for clients to omit and silently fall back to v1, hostile to scripted/agent consumers, hard to test from a browser or curl.
- Query-parameter versioning (`?version=2`) — rejected: collides with the chatbot-version `?version=` parameter the inspect endpoint uses.
- In-place rename with no version boundary — rejected: breaks confirmed external callers.
