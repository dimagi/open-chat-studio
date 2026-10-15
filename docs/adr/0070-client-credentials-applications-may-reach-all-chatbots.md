# ADR-0070: Client-credentials applications may reach all chatbots

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-10-15</p>

Amends: [ADR-0056](0056-client-credentials-applications-name-their-chatbots.md)

## Context

[ADR-0056](0056-client-credentials-applications-name-their-chatbots.md) limits a client-credentials application to the chatbots it lists. That fits a token placed in a chat widget, but not an integration that builds chatbots through the API. Such an integration can create a chatbot, but cannot edit or publish it until an admin adds it to the list by hand. It also cannot write source material or consent forms at all: every chatbot in the team shares them, so a token limited to some chatbots must not change them.

## Decision

We will add `OAuth2Application.all_data`, a boolean that defaults to `False`.

- When it is `True`, a client-credentials application reaches every chatbot in its team, including chatbots created later, and `allowed_chatbots` is ignored.
- Only such an application's tokens may create, change or archive source material and consent forms. A token limited to listed chatbots can still read them.
- When it is `False`, ADR-0056 applies unchanged: an empty list still means no chatbots.
- The field is audited, like `allowed_chatbots`, and is cleared for authorization-code applications.

## Consequences

- An integration can build a chatbot end to end (create it, write its resources, edit its pipeline, publish it) with one machine credential.
- A leaked secret from such an application exposes the whole team, as before ADR-0056, so admins must turn the flag on deliberately.
- The flag applies to every scope, `chat:start` included. A widget token from an all-chatbots application can start a chat with any chatbot in the team.

## Alternatives considered

- **Add each chatbot a token creates to its application's list** — rejected: it does not cover the shared resources, which belong to no chatbot.
- **Grant team-wide access through a new scope** — rejected: scopes say what a token may do, not which chatbots it may do it to (ADR-0056).
- **An access-mode choice field instead of a boolean** — rejected: there are only two modes, and the second one reaches past chatbots to the shared resources.
