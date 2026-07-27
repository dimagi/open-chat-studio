# ADR-0045: Ratchet widget auth level up on upgrade

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-07-21</p>

<p class="adr-meta">Extends: <a href="0044-durable-per-channel-widget-auth-policy.md">ADR-0044</a></p>

## Context

ADR-0044 made `ExperimentChannel.required_auth_level` a durable per-channel policy, set once by the grandfathering migration or the model default and never afterwards. Existing channels were seeded from the widget version they last reported. But a channel keeps reporting its version on every start (`widget_version`), so a channel grandfathered to `EMBED_KEY` from an old widget that later upgrades to a token-capable release still has its floor frozen at the weaker level — it goes on accepting weaker auth than its widget now supports, and nothing moves it up. A one-time seed cannot track a value that changes after the seed.

## Decision

A periodic task (`ratchet_widget_auth_levels`, daily) raises `required_auth_level` toward the `WidgetAuthLevel` the channel's last-reported `widget_version` can satisfy. The move is **monotonic** — it only ever raises the floor. A reported version is client-controlled and spoofable; allowing it to lower the floor would let an attacker strip a channel back to `NONE` by sending an old-version header, so a reported version may only tighten auth, never relax it.

The raise is two-phase, mediated by two new nullable columns, `pending_auth_level` and `auth_level_notified_at`:

- On first detecting an upgrade, record the pending level and notify the team (slug `widget-auth-level-upgrade`) with the minimum widget version every embed must run.
- Once the grace period `AUTH_LEVEL_RATCHET_GRACE` (14 days) elapses, apply the level and clear the pending state.

If the reported version drops back below the pending level before the grace period ends, the pending raise is abandoned. Applying the level goes through the audited write path; the pending-state bookkeeping bypasses auditing, like the `widget_version` telemetry it reacts to.

## Consequences

- A channel's floor now tracks its widget over time, not just at migration; upgraded channels stop accepting auth weaker than they can prove.
- The grace period plus the minimum-version notice give an operator time to bring *every* embed of a channel up to the floor before it tightens, since a single upgraded page does not prove the whole site upgraded.
- The trust decision is still durable and inspectable per ADR-0044; the ratchet is the only automated writer, and it cannot weaken a channel.
- Two columns and a daily task are added; the channel-details dialog shows the current minimum version and any pending raise.

## Alternatives considered

- **Ratchet inline on the start request** — rejected: it gives operators no warning window and puts a security-state write on the hot request path.
- **Apply immediately, no grace or notice** — rejected: one embed reporting a new version does not prove every embed upgraded; tightening without warning would break the laggards.
- **Re-infer the level per request from the version header** — rejected: that is the transient sniff ADR-0041 and ADR-0044 already retired.
- **Allow downgrades too** — rejected: the version header is spoofable, so a downward move is an attacker-triggerable auth bypass.
