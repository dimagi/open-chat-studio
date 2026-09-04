# ADR-0064: Channels may override the session token lifetime

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-09-02</p>

<p class="adr-meta">Extends: <a href="0054-chat-session-tokens-expire-on-absolute-age.md">ADR-0054</a>, <a href="0059-chat-api-channel-credential-mode.md">ADR-0059</a></p>

## Context

ADR-0054 set one global lifetime of 7 days and deferred a per-channel override. The two credential modes (ADR-0059) need different values. On a public `embed_key` widget, a mid-conversation restart has a cost and no benefit. On an `oauth` channel configured to limit abuse, admission should be re-checked within hours, because the usage available to one admitted caller is the rate limit multiplied by the lifetime (ADR-0054).

## Decision

We will add `ExperimentChannel.session_token_lifetime`, a nullable, audited duration. Null means the global `CHAT_SESSION_TOKEN_LIFETIME` applies. The field can shorten or lengthen the lifetime but cannot disable expiry.

## Consequences

- The value is read from the cached session's channel. A change takes effect when the cached session entry expires, and the lookup adds no query.
- A session with no channel uses the global value.
- When the lifetime expires on an `oauth` channel, the widget starts a new session through `chat/start/` with a token obtained from the host's `authTokenProvider`, so admission is re-checked.
- The recommended value for an `oauth` channel is between the OAuth token's own lifetime and one day. A shorter value causes restarts that reuse the current token without consulting the host.
- Inactivity-based expiry remains unavailable (ADR-0054).

## Alternatives considered

- **Global value only** → rejected: an `oauth` channel could not set a lifetime shorter than a week.
- **Allow null to mean no expiry** → rejected: ADR-0054 makes the lifetime mandatory.
- **A per-session message budget instead** → rejected in ADR-0054.
