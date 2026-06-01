# ADR-0023: Rename experiment to chatbot in the v2 API

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-29</p>
<p class="adr-meta">Extends: <a href="0022-url-path-api-versioning.md">ADR-0022</a></p>

## Context

"Chatbot" is the user-facing domain term; "Experiment" is the legacy internal model name. The external API is mid-migration and inconsistent: OAuth scopes and OpenAPI summaries already say "chatbot", but URLs, operation IDs, and most serializer fields still say "experiment". The v2 boundary from [ADR-0022](0022-url-path-api-versioning.md) is a clean point to finish the rename without disturbing existing callers.

## Decision

We will use `chatbot` throughout v2: the `/api/v2/chatbots/` route, `chatbot_*` operation IDs, the `"Chatbots"` OpenAPI tag, serializer field renames (`experiment` → `chatbot`, `experiment_id` → `chatbot_id`), and sessions nested at `/api/v2/chatbots/{id}/sessions/`. v2 also corrects other misleading names while the break allows it — notably a collection's embedding provider, stored internally as `llm_provider`, is surfaced as `embedding`. v1 keeps the `experiment` naming, frozen.

## Consequences

- The external vocabulary matches the product and the project glossary; the rename is API-surface only, leaving internal model names (`Experiment`) untouched.
- v1 and v2 payloads diverge in field names — the intended cost of a versioned break.

## Alternatives considered

- Rename in place with no version boundary — rejected: breaks existing callers (see [ADR-0022](0022-url-path-api-versioning.md)).
- Leave the experiment/chatbot inconsistency in v2 — rejected: API names ossify once consumed, so a half-done rename becomes permanent.
