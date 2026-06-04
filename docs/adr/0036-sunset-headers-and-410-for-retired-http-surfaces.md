# ADR-0036: Sunset headers and 410 Gone for retired HTTP surfaces

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-06-04</p>
<p class="adr-meta">Extends: <a href="0034-tiered-feature-deprecation-by-usage-audit.md">ADR-0034</a>, <a href="0022-url-path-api-versioning.md">ADR-0022</a></p>

## Context

Deprecating a public endpoint, API route, or webhook (first case: the iframe `/embed` public-chat endpoint) differs from deprecating an in-app feature (ADR-0034): the users are external callers who never see banners, notifications, or emails. They need machine-readable warning before removal and an explanation after it.

## Decision

We will sunset public HTTP surfaces with a standard response protocol:

- **During the deprecation window**, responses carry `Deprecation: true` and RFC 8594 `Sunset: <http-date>` headers, plus `Link: <successor-url>; rel="successor-version"` when a successor exists, applied via a shared view decorator.
- **Usage is audited from request logs** over the standard 90-day window, attributed to teams via URL parameters or authentication where possible.
- **At removal**, the URL returns `410 Gone` with a short body naming the replacement — never a silent 404. Where the replacement is a true drop-in, a permanent redirect is acceptable instead.
- **The `410` stub stays for at least one release cycle** before the route is deleted.
- **Versioned API routes (ADR-0022) deprecate per-version**: a v1 endpoint's sunset is announced in API docs and headers; old behaviour is never retrofitted into newer versions.

## Consequences

- External callers and their tooling get machine-readable advance warning and a successor pointer.
- Post-removal failures are self-explanatory: `410` with a replacement link instead of a mystery `404`.
- Dead routes linger in URL config for a release cycle as `410` stubs.
- Log-based attribution is best-effort on anonymous public endpoints; some affected callers can only be reached via the response headers themselves.

## Alternatives considered

- **Silent 404 after removal** → indistinguishable from a caller typo; pushes failures to support.
- **Permanent redirect for every removal** → masks semantic differences unless the replacement is a true drop-in.
- **Docs-only deprecation notices** → no machine-readable signal; callers who don't read docs break without warning.
