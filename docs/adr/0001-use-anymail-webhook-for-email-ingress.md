# ADR-0001: Use AWS SES + django-anymail signal handler for email ingress

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

## Context

We are adding email as a messaging channel in Open Chat Studio. Inbound email can reach the application by several mechanisms: IMAP polling against a mailbox, provider APIs (Microsoft Graph, Gmail API), webhooks from an Email Service Provider (AWS SES, SendGrid, Mailgun), or a hand-rolled MIME-parsing endpoint. Each option has different operational, security, and code-complexity trade-offs.

AWS SES is already configured for outbound mail in our production environment, and `django-anymail` is already a dependency that handles SES webhook signature validation, MIME parsing, and provider-specific normalization. Inbound email arriving at SES can be delivered via SNS to an HTTP webhook that anymail receives and translates into an `AnymailInboundMessage`, surfaced through anymail's `inbound` signal. We also need to strip quoted reply text and signatures so the LLM only sees new content.

## Decision

We will receive inbound email via AWS SES → SNS → django-anymail's `inbound` signal handler, parsing each `AnymailInboundMessage` through `mail-parser-reply` to strip quoted text and signatures before routing the message into the standard pipeline. The webhook does not run pipeline work synchronously; the signal handler parses, routes, and enqueues a Celery task so the HTTP response returns quickly to the ESP.

## Consequences

- **Positive:** No mailbox credentials or IMAP polling infrastructure. Webhook signature validation, MIME parsing, and provider normalization are handled by anymail, not by us. Switching ESPs in future requires anymail backend configuration, not new ingress code. The LLM sees clean text via `mail-parser-reply`'s 13-language reply/signature stripping.
- **Positive:** Same delivery substrate as outbound mail (SES is already set up), so DNS/SPF/DKIM/DMARC configuration is shared between directions.
- **Negative:** Coupled to django-anymail's signal API and to SES's SNS-webhook delivery model. A degraded webhook (SNS retry storms, signature changes) becomes a production incident.
- **Negative:** Adds `mail-parser-reply` as a new dependency.

## Alternatives considered

- **IMAP polling / "bring your own mailbox":** rejected — operational cost (per-channel credentials, polling latency, mailbox state), and incompatible with the self-service goal.
- **Microsoft Graph / Gmail API direct integration:** rejected — not ESP-agnostic, requires per-mailbox OAuth and tenant configuration; out of scope for v1.
- **Custom Django view for inbound webhook:** rejected — would duplicate signature validation and MIME parsing that anymail already provides.
- **`email_reply_parser` for quote stripping:** rejected — narrower language support and less active maintenance than `mail-parser-reply`.
