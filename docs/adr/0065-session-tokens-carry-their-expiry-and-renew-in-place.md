# ADR-0065: Session tokens carry their expiry and are renewed in place

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-09-10</p>

<p class="adr-meta">Extends: <a href="0039-require-proof-of-possession-for-chat-session-access.md">ADR-0039</a>, <a href="0060-each-credential-validates-its-own-origin.md">ADR-0060</a>, <a href="0061-bearer-token-authenticator-first-on-session-start-only.md">ADR-0061</a>, <a href="0063-session-start-requires-a-chat-start-machine-token.md">ADR-0063</a>, <a href="0064-per-channel-session-token-lifetime-override.md">ADR-0064</a></p>

<p class="adr-meta">Supersedes: <a href="0054-chat-session-tokens-expire-on-absolute-age.md">ADR-0054</a> (its reference point; expiry on age rather than inactivity stands)</p>

## Context

ADR-0054 expires a session's token a fixed time after the session was created. The time is computed server-side from the session, and the token carries only the session id. ADR-0064 lets a channel shorten the lifetime, and on an `oauth` channel expiry sends the widget back through `chat/start/` for a new session. Every expiry therefore ends the conversation: history stays on the old session, and a bound page cannot restart at all. ADR-0061 keeps the bearer token off the session-bound endpoints, so nothing after session start can re-check the host's admission without starting over.

Because the deadline belongs to the session, a token cannot be reissued: every token for the session dies at the same instant.

## Decision

We will stamp the expiry into the token and measure the lifetime from issuance.

- The signed payload gains an `exp` claim, set at issuance to now plus the channel's `session_token_lifetime` or `CHAT_SESSION_TOKEN_LIFETIME` (ADR-0064). The claim alone decides expiry. A token without the claim, issued before this decision, falls back to ADR-0054's rule.
- `POST /api/chat/start/` returns `expires_at` beside the token.
- `POST /api/chat/<session_id>/token/` issues a fresh token for an existing session and returns it with `expires_at`. Admission is that of session start (ADR-0060, ADR-0062, ADR-0063): a client-credentials token with `chat:start` for the session's chatbot, a channel in `oauth` mode, and the channel's origin rule. No other credential is accepted, so a session token cannot renew itself.
- Renewal re-reads the channel from the database rather than the cached session, so a deleted, disabled or reconfigured channel takes effect at the next renewal.
- Renewal requests are throttled per OAuth application, not per session.
- On an `oauth` channel the widget renews when its token expires, using a fresh bearer token from the host. It starts a new session only when renewal is refused.

## Consequences

- The lifetime bounds a token, not a conversation. A conversation lasts as long as the host keeps renewing, and each renewal re-checks admission.
- Tightening a channel's lifetime applies to tokens issued after the change; sessions already running keep their current token until it expires. Disabling the channel or removing the chatbot from the application's allowlist stops renewals immediately.
- Bound pages and "Continue chat" mint a token on render, so an old session opened by its owner works again. ADR-0054's follow-up for those pages is closed.
- A second endpoint carries the bearer authenticator, which ADR-0061 restricted to session start. Its reasons still hold: the chatbot is resolved from the session in the URL, and `request.auth` holds the access token rather than a channel, so the legacy-access check cannot read it as an embed key.
- ADR-0061 deferred throttling per OAuth application. It now exists for renewals only; session start stays per channel.
- On a browser-facing channel the renewal must come from a listed origin (ADR-0060), so the widget makes the call, not the host's backend.
- `embed_key` channels have no credential to renew with, so they still restart on expiry as ADR-0054 describes.
- Tokens without the claim keep working under the old rule until they expire. Nothing is migrated.

## Alternatives considered

- **Store a renewable deadline on the session** → rejected: it makes the token stateful, and every bound page relies on re-deriving it (ADR-0040).
- **Let the session token renew itself** → rejected: a leaked token could keep itself alive, and renewal exists to re-check the host's admission.
- **Renew by calling `chat/start/` again** → rejected: that creates a new session and participant, which is the restart this decision removes.
- **Require the bearer token on every session-bound request** → rejected in ADR-0061.
- **Take the channel from the cached session at renewal** → rejected: the snapshot can be a cache TTL old, and a token minted from a stale lifetime extends the change by a full token lifetime.
