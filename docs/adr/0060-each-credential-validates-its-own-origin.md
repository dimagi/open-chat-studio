# ADR-0060: Each credential validates its own origin

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-09-02</p>

<p class="adr-meta">Extends: <a href="0053-chat-session-start-requires-membership-or-embed-key.md">ADR-0053</a>, <a href="0059-chat-api-channel-credential-mode.md">ADR-0059</a></p>

## Context

ADR-0053 made the domain check part of embed-key validation: a key is accepted only together with an `Origin` or `Referer` header that matches the channel's `allowed_domains`. A request with no origin header is refused, because browsers always send one and a request without one did not come from an embed.

A bearer token needs a different rule. A server integration sends no origin header and should not be required to. A browser embed in `oauth` mode does send one, and a token copied from that page should be accepted from no more origins than the embed key would be. The channel needs a way to distinguish the two deployments. The `PUBLIC` platform (ADR-0059) is served from the OCS host only and has no domain list.

## Decision

We will apply the origin rule of whichever credential admits the caller, and use the channel's domain list to distinguish a browser-facing `oauth` channel from a server-only one.

| Mode | `allowed_domains` | `Origin` or `Referer` present | Neither present |
|---|---|---|---|
| `embed_key` | required | must match | reject |
| `oauth`, list non-blank | optional | must match | reject |
| `oauth`, list blank | optional | reject | admit |

- A blank list on an `oauth` channel means server-only. Every request with an `Origin` or `Referer` header is refused, regardless of the token. `allowed_domains` is required in `embed_key` mode and optional in `oauth` mode. The form's help text states that a blank list means server-only.
- The origin rule is applied once, by the credential that resolves the channel. No later check re-evaluates a token-resolved channel, because the blank-list "admit" row cannot be expressed as a domain match.
- A `PUBLIC` channel's origin must equal the canonical Site hostname. The comparison is hostname to hostname, so ports are ignored.
- After session start, the rule follows the credential presented. A session token has no origin semantics (ADR-0039). An embed key sent with a session-bound request is checked against the domain list in the usual way.

## Consequences

- A browser-facing `oauth` channel refuses requests with no origin header or an unlisted origin. This is the protection the rejected key-and-token mode would have provided (ADR-0059). Browsers set `Origin` and scripts can set it to any value, so this limits replay from browsers on other sites, not replay from a script.
- A non-browser client that sends a `Referer` header to a blank-list `oauth` channel is refused. The integration documentation must state this.
- Admins may read "optional" as "not set", so the form must explain that a blank list means server-only.
- Applying the domain list to session-token requests is possible later hardening. It would change behaviour for existing widget sessions.

## Alternatives considered

- **A separate server-integration setting on the channel** → rejected: the domain list already carries that information.
- **Require "allow all domains" for a server integration** → rejected: it misdescribes the deployment and admits requests from any browser.
- **Decide originless handling by mode alone** → rejected: an `oauth` channel with a domain list must still refuse originless requests.
- **Apply the domain list to session-token requests** → deferred: the session token is the credential for those requests and carries no origin.
