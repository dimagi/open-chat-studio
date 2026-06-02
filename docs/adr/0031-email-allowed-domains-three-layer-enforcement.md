# ADR-0031: Enforce email allowed-domains at three layers

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-06-02</p>
<p class="adr-meta">Extends: <a href="0030-email-channel-allowed-domains-global-setting.md">ADR-0030</a></p>

## Context

ADR-0030 made `EMAIL_CHANNEL_ALLOWED_DOMAINS` the source of truth for which recipient domains the email channel handles. That setting has to be enforced wherever a disallowed domain could enter the system: at inbound delivery (before any background work is queued), at channel configuration (so operators cannot save a channel that will never receive mail), and at the platform-availability UI (so the feature is hidden when it cannot be used).

## Decision

We will enforce the allowlist at three layers:

- **Inbound pre-filter.** `email_inbound_handler` calls `is_email_domain_allowed(to_address)` after parsing the message and before enqueueing the Celery handler. Mail to a disallowed domain is dropped with an INFO log.
- **Form validation.** `EmailChannelForm` uses `clean_email_address` and `clean_from_address` (per-field, not `clean()`) so errors attach to the offending field. The error message lists the configured allowed domains; the field help text shows them as well. `from_address` is only validated when set.
- **Platform availability.** `ChannelPlatform.for_dropdown` removes `EMAIL` from the channel-type dropdown when `EMAIL_CHANNEL_ALLOWED_DOMAINS` is empty (independently of the existing `flag_email_channel` feature flag).

The inbound filter applies to **all** inbound messages, including replies on existing threads. Removing a domain from the allowlist must take effect immediately and stop in-flight conversations rather than honour a grace period.

## Consequences

- Each entry point is blocked at the earliest layer it can be, so the system never reaches a partial state where a channel exists but its mail is silently dropped — that combination is only possible for pre-existing channels whose domains were removed.
- Form errors echo the configured domains, so operators see what to use without consulting docs.
- The inbound filter runs on every webhook including replies, so a domain removal interrupts ongoing conversations.
- Pre-existing channels for now-disallowed domains keep loading but cannot be re-saved; their inbound mail is silently dropped until they are reconfigured.

## Alternatives considered

- **Enforce only at form save** → rejected: the default-fallback channel from ADR-0002 accepts mail for any address, so without an inbound filter the system would process mail for domains it does not own.
- **Enforce only at inbound** → rejected: operators could save an `email_address` that will never receive mail, surfacing the misconfiguration only after the first inbound test.
- **Use `clean()` instead of `clean_<field>`** → rejected: per-field errors attach to the right form field for the user.
- **Honour in-flight replies on disallowed domains** → rejected: requires per-message exception logic and leaves a tail of accepted mail after a removal.
