# ADR-0061: The bearer token is resolved by an authenticator, first in the list, on session start only

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-09-02</p>

<p class="adr-meta">Extends: <a href="0039-require-proof-of-possession-for-chat-session-access.md">ADR-0039</a>, <a href="0052-app-layer-rate-limiting-mechanism.md">ADR-0052</a>, <a href="0053-chat-session-start-requires-membership-or-embed-key.md">ADR-0053</a>, <a href="0059-chat-api-channel-credential-mode.md">ADR-0059</a></p>

## Context

ADR-0053 placed the start-session authorization rule in one function. OAuth could be added to that function or checked separately, and the choice depends on two DRF behaviours. DRF stops at the first authenticator that returns a result. The chat API throttle (mechanism in ADR-0052) keys its bucket on the channel it finds in `request.auth`. If the token is resolved anywhere other than an authentication class, `request.auth` stays empty and all machine callers behind one egress IP share the anonymous IP bucket.

Snippets switched to `oauth` mode continue to send `X-Embed-Key`. If the embed-key authenticator ran first, it would match and the token would not be read.

## Decision

We will resolve the bearer token in a dedicated authentication class placed first in the authentication classes of `POST /api/chat/start/`. No other endpoint uses it.

- It returns an anonymous user and the chatbot's `oauth`-mode channel, the same result shape as the embed-key authenticator. This puts the channel in `request.auth`.
- With no `Authorization` header, or an unknown chatbot id, it returns nothing and the session and embed-key authenticators run. If a header is present for a known chatbot and any check fails, it raises. An invalid token is never treated as an absent token.
- It resolves working versions only, so a version's `public_id` is treated as unknown. A request with no `X-Embed-Key` then receives the view's 404. A request that also carries an embed key is refused by the embed-key authenticator instead (ADR-0062).
- The ADR-0053 authorization function gains an OAuth branch. Its embed-key branch refuses a key that resolves an `oauth`-mode channel when no token is present (ADR-0059). A token does not permit version selection; `version_number` remains member-only (ADR-0053).
- The channel the token resolved owns the session. Widget version recording remains conditional on widget requests, so a server caller is not recorded with a placeholder version.
- The four session-bound endpoints keep their authentication classes and ADR-0039's rules. The bearer token is not required after session start.

## Consequences

- OAuth traffic is throttled per channel with no change to the throttle. Two integrations on one chatbot share an allowance. A `PUBLIC` channel throttles session starts per visitor IP instead.
- The 401 status results from the authenticator's position (ADR-0062).
- Reordering the authenticators has two effects. If the embed-key authenticator ran first, a request carrying both `Authorization` and `X-Embed-Key` would be admitted by the key and the token would not be validated. If session authentication ran first, refusals would return 403 instead of 401, because it supplies no `WWW-Authenticate` value.
- A session created with a token can be continued with the session token alone. A short-lived OAuth token therefore cannot expire mid-conversation.
- An `Authorization` header on session start was previously ignored. It now produces a 401 when it is not a valid `chat:start` token.
- A rejected token is not counted by the credentials throttle, because DRF authenticates before it throttles. A per-IP fail-closed bucket would also count ADR-0054's legitimate re-admission requests, so this is left to ADR-0052.

## Alternatives considered

- **A resolver called from the view** → rejected: `request.auth` stays empty, OAuth traffic falls into the IP bucket, and the 401 requires a workaround.
- **Place it after the embed-key authenticator** → rejected: snippets still send the key, so the token would never be validated.
- **Treat an invalid token as no token** → rejected: a revoked token would fall through to the keyless path and be admitted.
- **Require the bearer token on session-bound requests** → rejected: the host would have to keep a valid token in page JavaScript for the whole conversation, and the legacy-access check would treat a token-resolved channel as an embed key.
- **Throttle per OAuth application** → deferred: per-channel matches how widget traffic is throttled; per-application is an additional identity case for ADR-0052.
