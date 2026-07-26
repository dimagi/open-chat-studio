# ADR-0043: BSUID participant identity for WhatsApp

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-06-26</p>

## Context

Meta is rolling out business-scoped user IDs (BSUIDs) for WhatsApp — a stable per-business
identifier (e.g. `US.13491208655302741918`) sent on every post-rollout webhook. The phone
number is now optional: Meta omits it once a user adopts a WhatsApp username, and Twilio
likewise sends the BSUID (`ExternalUserId`) alongside the phone. Historically OCS keyed
WhatsApp participants by phone number, so we need an identity model for the BSUID era that
keeps a stable key, preserves continuity for users already keyed by phone, and can still
address outbound messages — Meta and Twilio do not yet accept a BSUID as a send recipient.

## Decision

We will treat the BSUID as the participant identifier and the phone number as a secondary,
optional attribute:

- New WhatsApp participants are keyed by their BSUID. When a message carries no BSUID
  (Turn.io, or pre-rollout traffic), the participant is keyed by phone number as before.
- Existing phone-keyed participants are left unchanged — matched on inbound, never renamed
  or migrated to their BSUID.
- Participant lookup matches on the BSUID **or** the inbound phone number, with the oldest
  matching row winning, so a returning user previously keyed by phone is reused rather than
  forked onto a new BSUID row.
- The phone number is persisted on the participant's existing `remote_id` field, not in the
  participant `data` JSON.
- Outbound sends use the stored phone (`remote_id`) as the recipient, falling back to the
  identifier when no phone is known. The Meta payload routes a phone to its `to` field and a
  BSUID to its `recipient` field, keeping the path forward-compatible for when Meta accepts
  BSUID recipients.

## Consequences

- Conversation continuity is preserved for phone-keyed users with no data migration —
  `remote_id` already exists on the participant.
- The BSUID is a stable key even when a user's phone changes or is withheld.
- Storing the phone on `remote_id` keeps it out of the LLM prompt context for free, since
  that field is not part of the participant `data`.
- Two identifier schemes coexist indefinitely (BSUID rows and legacy phone rows); every
  participant lookup must consider both.
- Reaching a participant still depends on a known phone: a BSUID-only participant cannot be
  messaged until providers accept BSUID recipients.
- Twilio accepts only phone numbers as recipients; the BSUID-recipient routing stays dormant
  until Meta supports it.

## Alternatives considered

- Key participants by phone with the BSUID as fallback (the original branch's approach) →
  rejected: the phone is optional post-rollout and can change, so it is not a stable key.
- Migrate existing phone-keyed participants to their BSUID → rejected: a large, risky backfill
  for no functional gain, since lookup-time matching already gives continuity.
- Store the phone in the participant `data` JSON → rejected: it would leak into the LLM prompt
  context and require extra filtering.
- Send directly to the BSUID → rejected: Meta and Twilio do not accept BSUIDs as outbound
  recipients yet.
