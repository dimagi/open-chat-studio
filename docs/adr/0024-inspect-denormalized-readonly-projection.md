# ADR-0024: `/inspect/` as a denormalized read-only projection on a distinct URL

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>
<p class="adr-meta">Extends: <a href="0022-url-path-api-versioning.md">ADR-0022</a></p>

## Context

An external agent needs a chatbot's full configuration in a single read. These endpoints are read-only now, but write capabilities are planned. The risk in shipping reads first is that a rich read shape placed on the canonical resource URL leads clients to assume it is round-trippable — that they can PATCH whatever they GET — which would constrain the eventual write shape.

## Decision

We will serve the rich configuration at a dedicated action URL, `GET /api/v2/chatbots/{id}/inspect/`, kept separate from the plain `GET /api/v2/chatbots/{id}/`. `/inspect/` is explicitly a denormalized, read-only projection; the plain resource stays minimal and is where a future PATCH lands. The endpoint accepts a `?version=` parameter to inspect a specific published version or the working draft, defaulting to the working version.

## Consequences

- Consumers get an unambiguous signal that inspect is a view, not a representation; the future write API is free to take a different, normalized shape.
- A chatbot now has two read shapes (minimal canonical + rich inspect) to maintain.

## Alternatives considered

- `/export/` naming — rejected: implies a transferable, re-importable artifact, which this projection is not.
- Putting the rich shape on the canonical `GET` of the resource — rejected per the read-constrains-write risk above.
