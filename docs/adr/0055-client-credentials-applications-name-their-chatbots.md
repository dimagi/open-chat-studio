# ADR-0055: Client-credentials applications name the chatbots they may reach

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-08-14</p>

## Context

Client-credentials OAuth applications are pinned to a team at registration, and every token they issue is scoped to that team. Until now the team was the *only* boundary: a machine token bearing `chatbots:interact` was authorised to reach every chatbot in its team, through chat completions and the message-ingress endpoint, with nothing per-chatbot enabling it. The same scope also authorises outbound messages to arbitrary participants on WhatsApp, Telegram and Connect.

A team is the wrong granularity for a credential with no user behind it: teams hold unrelated chatbots, and an application is typically registered for one integration. The gap widens with the chat widget, where the supported shape places a bearer token in page JavaScript by design, putting a team-wide grant one page-source leak away.

## Decision

We will scope a client-credentials application to an explicit set of chatbots.

- `OAuth2Application.allowed_chatbots`, a many-to-many to `Experiment`, names the chatbots the application may reach. It is audited, so changes to an application's reach are recorded.
- **Empty means none, not all.** An application authorises nothing until someone says so.
- It is consulted for **client-credentials callers only**. API-key, Django-session and authorization-code callers keep team-membership semantics untouched, so the field neither appears on nor constrains a user-facing application.
- It gates every door `chatbots:interact` opens, not one of them: chat completions, message ingress, and outbound bot messages.
- Denial is a `403` — the caller authenticated, it simply is not authorised for this chatbot — raised before anything with a side effect exists, so a denied call leaves no session and no participant data behind.
- The allowlist holds **working versions**. A caller may legitimately address a chatbot version by its own `public_id`, so the check normalises to the version family head; listing a chatbot therefore authorises all of its versions.
- The allowlist lives on the application, not on the channel. `oauth` sits in the entry-point tier and `channels` in domain & runtime, so a reference from `channels` would invert the dependency direction while `oauth` → `experiments` does not.

## Consequences

- Existing client-credentials applications stop reaching any chatbot until an admin names one. There is no backfill: client credentials shipped 2026-07-24, so the live population migrates by announcement.
- The reach of a machine credential is now auditable in one place, rather than inferred from team membership.
- Scope and allowlist answer different questions — the scope says what a token may *do*, the allowlist says which chatbots it may do it *to*. Both must agree.
- It bounds which chatbots a token reaches, not which APIs, so a per-application *scope* restriction remains an open gap.
- Where a chatbot is also exposed through a channel, its reachability is described in two places, and the admin who registers an application is often not the one who configures the channel.

## Alternatives considered

- **Keep the team as the only boundary** — rejected: it grants an unattended credential every chatbot its team owns, including outbound messaging to arbitrary participants.
- **Put the allowlist on the channel** — rejected: it inverts the dependency direction between `channels` and `oauth`, and scopes a credential from the wrong side.
- **Empty means all** — rejected: backwards compatible, but it makes the safe configuration the one an admin has to remember, which is the opposite of deny-by-default.
- **Backfill every existing application with its team's chatbots** — rejected: it would preserve the too-coarse grant this ADR exists to withdraw, under a new name.
- **A narrower scope instead of an allowlist** — rejected: scopes are team-wide by construction and say what a caller may do, not which chatbot it may do it to.
