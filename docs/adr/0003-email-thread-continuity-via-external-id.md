# ADR-0003: Email thread continuity via ExperimentSession.external_id

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

<p class="adr-meta">Extends: <a href="0002-email-channel-slack-style-routing.md">ADR-0002</a></p>

## Context

Once routing has identified the right experiment channel, the system needs to decide whether an inbound email belongs to an existing `ExperimentSession` or should start a new one. The Slack channel solves the analogous problem by storing a composite of `slack_channel_id` and `thread_ts` on `ExperimentSession.external_id`, which is a `CharField(max_length=255, unique=True)` with an existing unique index. Email's natural analog is the `Message-ID`: each outbound mail has a unique one, and reply clients set `In-Reply-To` pointing at the parent's `Message-ID`.

We have several places we could keep that mapping: the existing `external_id` column, a new join table dedicated to email threads, or a JSON structure inside `ExperimentSession.extra_data`.

## Decision

We will store the first outbound `Message-ID` for an email conversation on `ExperimentSession.external_id`, and on inbound mail look it up first against `In-Reply-To` and then against each entry in `References`. No new tables, no JSON queries, no per-message `Message-ID` persistence.

## Consequences

- **Positive:** Reuses an indexed, unique column that already exists for exactly this kind of lookup; no schema change beyond the `ChannelPlatform` enum addition.
- **Positive:** Same shape as the Slack channel's session lookup, so cross-channel consistency is preserved.
- **Positive:** RFC 2822 mandates that the root `Message-ID` always appears in the `References` header even when the chain is truncated, so the fallback scan reliably catches replies to interior thread messages.
- **Negative:** Only the first outbound `Message-ID` is persisted. If a future feature needs to thread off an arbitrary message inside the conversation, we will need a separate model.
- **Negative:** `external_id` becomes overloaded across channels: Slack stores a `channel_id:thread_ts` composite, email stores a raw `Message-ID`. Each channel must know how to interpret its own format.

## Alternatives considered

- **New `EmailThread` model with `Message-ID` index and FK to `ExperimentSession`:** rejected — adds a whole table for a single string field's worth of value; the routing chain only ever needs the root `Message-ID`.
- **List of `Message-ID`s in `ExperimentSession.extra_data` (JSON):** rejected — JSON containment lookups are slower than the indexed `CharField`, and we have the column already.
- **Store the latest outbound `Message-ID` (not the first):** rejected — would need updating on every reply and would not survive replies sent to an older thread message; the first `Message-ID` is always in `References`, so storing the first is strictly more robust.
