# ADR-0030: Gate the email channel with a global allowed-domains setting

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-02</p>

## Context

The email channel (ADR-0001) accepts mail for any recipient that reaches the SES endpoint, especially through the default-fallback routing path (ADR-0002, `extra_data.is_default=True`). Without bounds, the system would accept and try to process mail for arbitrary domains. We need a single source of truth for which recipient domains the platform is willing to handle, and operators need a way to configure it without code or per-team UI.

## Decision

We will introduce a Django setting `EMAIL_CHANNEL_ALLOWED_DOMAINS`, loaded from an environment variable as a comma-separated list, that defines the allowed recipient domains for the email channel platform-wide.

- Wildcard subdomain patterns (`*.example.com`) are supported, reusing the matching rule from the embedded-widget origin allowlist.
- Empty / unset = deny everything (fail-closed): no inbound is accepted and no email channel can be saved.
- Domain comparison is case-insensitive.
- Only the recipient (`to_address` on inbound, `email_address` and optional `from_address` on the form) is gated; the sender (`From:`) is not validated.

## Consequences

- Deploying the email feature requires setting the env var; an unconfigured deploy produces a visibly broken email feature, not silent acceptance.
- One allowlist serves all teams; adding a new team's domain is a deploy action, not self-service.
- Removing a domain from the allowlist takes effect on the next request, including replies on existing threads.
- Existing channels for addresses outside the new allowlist keep loading but can no longer be saved; operators reconcile manually (no auto-disable, no data migration).
- Rejected inbound mail is dropped silently with a log line — senders receive no auto-reply.

## Alternatives considered

- **Per-team or per-channel allowlists** → rejected: out of scope for v1; a single global setting matches the current operator model.
- **Default to allowing all domains** → rejected: the default-fallback channel from ADR-0002 means an unconfigured allowlist would accept mail to any address the SES endpoint reaches.
- **Also validate the sender domain on inbound** → rejected: sender-side restrictions are a separate concern, not part of this gate.
- **Auto-reply on rejection** → rejected: noisy and abusable; a silent drop with a log line is sufficient.
- **Auto-disable or migrate existing channels whose addresses fall outside the new allowlist** → rejected: keep the change additive; operators reconcile manually.
