# ADR-0001: Use AWS SES + django-anymail signal handler for email ingress

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

## Context

We are adding email as a messaging channel. Inbound email can arrive via IMAP polling, provider APIs (Microsoft Graph, Gmail), ESP webhooks (SES, SendGrid, Mailgun), or a hand-rolled MIME endpoint, each with different operational, security, and complexity trade-offs.

AWS SES already handles our outbound mail, and `django-anymail` is already a dependency that validates SES webhook signatures, parses MIME, and normalizes provider payloads into an `AnymailInboundMessage` exposed via its `inbound` signal. We also need to strip quoted reply text and signatures so the LLM sees only new content.

## Decision

We will receive inbound email via AWS SES → SNS → django-anymail's `inbound` signal handler.

- Parse each `AnymailInboundMessage` through `mail-parser-reply` to strip quoted text and signatures.
- Route the cleaned message into the standard pipeline.
- Enqueue a Celery task rather than run pipeline work synchronously, so the HTTP response returns quickly to the ESP.

## Consequences

- **Positive:** No mailbox credentials or IMAP polling; anymail handles signature validation, MIME parsing, and provider normalization.
- **Positive:** Switching ESPs requires anymail backend config, not new ingress code.
- **Positive:** Shares the outbound SES substrate, so DNS/SPF/DKIM/DMARC config is shared across both directions.
- **Negative:** Coupled to anymail's signal API and SES's SNS-webhook delivery; a degraded webhook becomes a production incident.
- **Negative:** Adds `mail-parser-reply` as a new dependency.

## Alternatives considered

- **IMAP polling / bring-your-own-mailbox** → rejected: per-channel credentials, polling latency, mailbox state, and incompatible with the self-service goal.
- **Microsoft Graph / Gmail API direct integration** → rejected: not ESP-agnostic, needs per-mailbox OAuth, out of scope for v1.
- **Custom Django view for inbound webhook** → rejected: duplicates signature validation and MIME parsing anymail already provides.
- **`email_reply_parser` for quote stripping** → rejected: narrower language support and less active maintenance than `mail-parser-reply`.
