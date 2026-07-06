# ADR-0003: Email thread continuity via ExperimentSession.external_id

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

<p class="adr-meta">Extends: <a href="0002-email-channel-slack-style-routing.md">ADR-0002</a></p>

## Context

After routing identifies the experiment channel ([ADR-0002](0002-email-channel-slack-style-routing.md)), the system must decide whether an inbound email belongs to an existing `ExperimentSession` or starts a new one. Email's natural thread key is the `Message-ID`: each outbound mail has a unique one, and replies set `In-Reply-To` and `References` pointing at ancestors. The Slack channel solves the analogous problem by storing a `slack_channel_id`/`thread_ts` composite on `ExperimentSession.external_id`, a `CharField(max_length=255, unique=True)` with a unique index.

We could keep the email mapping in the existing `external_id` column, a new join table, or JSON inside `ExperimentSession.extra_data`.

## Decision

We will store the first outbound `Message-ID` on `ExperimentSession.external_id`. On inbound mail we look it up against `In-Reply-To` first, then against each entry in `References`. No new tables, no JSON queries, no per-message `Message-ID` persistence.

## Consequences

- **Positive:** Reuses an existing indexed, unique column; no schema change beyond the `ChannelPlatform` enum addition.
- **Positive:** Same lookup shape as the Slack channel, preserving cross-channel consistency.
- **Positive:** RFC 2822 mandates the root `Message-ID` always appears in `References` even when the chain is truncated, so the fallback scan catches replies to interior thread messages.
- **Negative:** Only the first outbound `Message-ID` is persisted; threading off an arbitrary interior message would require a separate model.
- **Negative:** `external_id` is overloaded across channels (Slack composite vs. raw email `Message-ID`), so each channel must interpret its own format.

## Alternatives considered

- **New `EmailThread` model with `Message-ID` index and FK to `ExperimentSession`** → rejected; a whole table for one string's worth of value, when routing only needs the root `Message-ID`.
- **List of `Message-ID`s in `ExperimentSession.extra_data` (JSON)** → rejected; JSON containment lookups are slower than the indexed `CharField`, which we already have.
- **Store the latest outbound `Message-ID` instead of the first** → rejected; needs updating on every reply and breaks for replies to older thread messages, whereas the first is always in `References`.
