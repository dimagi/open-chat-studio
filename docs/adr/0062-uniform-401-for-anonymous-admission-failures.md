# ADR-0062: Anonymous admission failures at session start return one uniform 401

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-09-02</p>

<p class="adr-meta">Extends: <a href="0053-chat-session-start-requires-membership-or-embed-key.md">ADR-0053</a>, <a href="0061-bearer-token-authenticator-first-on-session-start-only.md">ADR-0061</a></p>

## Context

DRF converts an authentication failure to 403 unless the first authenticator in the list returns a value from `authenticate_header`. Session authentication returns none, so every refusal at session start, including an invalid embed key, returned 403.

The widget's error handling reads a `code` field from the response body, which DRF does not supply by default. The start endpoint has several reasons to refuse a request: chatbot not exposed, invalid or expired token, wrong team, missing scope, application not allowed, disallowed origin, or an embed key presented to an `oauth` channel.

## Decision

We will return one response for every admission failure that the start endpoint's own authorization raises for an anonymous caller.

- Status 401, body `{"error": "Authentication required to chat with this chatbot", "code": "chat_access_denied"}`. The failed check is not included in the response.
- The 401 status results from the bearer-token authenticator being first and returning `Bearer realm="api"` (ADR-0061). The exception supplies the body.
- Two refusals are outside this rule. An embed key that matches no channel is refused by the embed-key authenticator with DRF's default 401 body. A `version_number` from an anonymous caller, with or without a token, returns the 403 from ADR-0053's member-only rule.
- Authenticated callers keep the 403 and the body "You do not have access to this chatbot". This includes a logged-in non-member whose embed key resolves an `oauth`-mode channel. A 401 to a logged-in user would indicate a broken session.
- An unknown chatbot id returns 404 when the request carries no embed key. Chatbot `public_id`s are not secret (ADR-0053), and the 404 is the only indication of a mistyped id. A request that also carries an embed key is refused by the embed-key authenticator before the view runs (ADR-0061).
- The application allowlist denial is a 401 here, unlike the 403 in ADR-0056. The start endpoint calls the allowlist predicate and lets the authenticator raise, because the enforcing helper's response would identify the failed check.

## Consequences

- An invalid embed key on session start now returns 401 instead of 403. The session-bound endpoints still return 403, because their authentication classes are unchanged.
- A misconfigured integrator receives the same response as an attacker. The channel dialog lists the applications whose allowlist includes the chatbot and warns when there are none.
- No log records which check failed. Diagnosing a refused integration means checking the channel mode, domain list, token scope and allowlist by hand. Server-side logging of the failed check is a gap this decision leaves open.
- The widget's session-token recovery is unaffected, because the session endpoints raise a permission error that DRF does not convert.

## Alternatives considered

- **A dedicated exception with its own `WWW-Authenticate` header** → rejected: the authenticator's position produces the 401 (ADR-0061).
- **A distinct `code` per denial reason** → rejected: it tells a caller which check to work around.
- **Return 401 for an unknown chatbot** → rejected, see Decision.
- **403 for every anonymous refusal** → rejected: 401 is the correct status for a missing or invalid credential, and it is what the widget's recovery path expects.
