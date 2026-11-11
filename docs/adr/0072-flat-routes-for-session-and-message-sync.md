# ADR-0072: Flat routes for session and message sync

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-11-11</p>

Supersedes, for session and message reads: the nested sessions route in [ADR-0023](0023-rename-experiment-to-chatbot-in-v2.md).

## Context

[ADR-0023](0023-rename-experiment-to-chatbot-in-v2.md) renamed `experiment` to `chatbot` across v2 and, in the same sentence, placed sessions at `/api/v2/chatbots/{id}/sessions/`. The route was never built. Its alternatives discuss only naming; no resource shape was weighed.

[#3904](https://github.com/dimagi/open-chat-studio/issues/3904) asks for incremental session and message reads for warehouses and CRMs. A sync consumer wants every change in the team since its last pull. Nested routes make it call once per chatbot per cycle, and find new chatbots on its own, before it can know nothing changed.

## Decision

We will serve session and message reads from flat, team-scoped collections:

- `GET /api/v2/sessions/` and `GET /api/v2/sessions/{id}/`
- `GET /api/v2/messages/`

`chatbot` is an optional query parameter on both collections, not a path segment. The rest of ADR-0023 (the `chatbot` naming and field renames) stands.

## Consequences

- A consumer syncs a team with one paginated pull per collection per cycle, whatever the number of chatbots.
- Narrowing to one chatbot is `?chatbot=<public_id>`, so the same endpoint serves both uses.
- v2 has no `/chatbots/{id}/sessions/` route; a later need for one adds it as an alias of the filtered collection.
- Authorization stays team-scoped, as in [ADR-0028](0028-inspect-authorization-team-scoped.md); the chatbot filter narrows results, it is not an access boundary.

## Alternatives considered

- Nested routes as ADR-0023 states: rejected for sync, as it multiplies calls by the number of chatbots and needs chatbot discovery each cycle.
- Both nested and flat routes: rejected, two documented paths to the same rows for no consumer that needs both.
