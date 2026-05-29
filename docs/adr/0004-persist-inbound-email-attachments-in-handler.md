# ADR-0004: Persist inbound email attachments in the webhook handler

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

<p class="adr-meta">Extends: <a href="0001-use-anymail-webhook-for-email-ingress.md">ADR-0001</a>, <a href="0002-email-channel-slack-style-routing.md">ADR-0002</a></p>

## Context

Phase 2 adds bidirectional attachment support to the email channel. Inbound email arrives via the anymail webhook handler (ADR-0001) and is routed through the Slack-style priority chain (ADR-0002), which previously ran in a Celery task behind a best-effort webhook pre-filter.

Attachments raise the question of where the bytes get persisted. Persisting in the Celery task sends raw blobs through the Redis broker, forces team context to be re-derived, and saves files for emails that route to no channel. The records must reach the pipeline and LLM as `Attachment` objects with session-scoped download links.

## Decision

We will persist inbound attachments synchronously in the webhook handler, promoting it to the full router:

- The handler resolves channel and session, sets team context, and calls `File.create()`, so the Celery payload carries only File IDs.
- A generic `BaseMessage.attachment_file_ids` field carries those IDs.
- A generic `AttachmentHydrationStage`, inserted into the default pipeline after session resolution and before chat-message creation, converts the IDs into `Attachment` objects with session-scoped download links.

## Consequences

- No blobs through the Celery broker; payloads stay small and team context is known at persistence time.
- No orphaned files — if routing finds no channel, nothing is saved.
- `attachment_file_ids` and `AttachmentHydrationStage` are channel-agnostic, so any future channel that pre-persists inbound files reuses the same plumbing.
- Hydrating after session resolution guarantees download links point at a real session.
- **Negative:** the handler now routes and runs `File.create()` synchronously before returning `200` to the ESP, raising webhook latency.
- **Negative:** `handle_email_message` gains `channel_id`/`session_id` arguments, requiring a one-release-cycle legacy fallback for in-flight pre-deploy tasks.

## Alternatives considered

- **Persist in the Celery task** → rejected: raw bytes through the broker, team-context re-derivation, and orphaned files on no-match.
- **Email-specific hydration instead of a generic field + stage** → rejected: other v2-migrated channels will want the same pre-persist pattern.
- **A new `FilePurpose` for email attachments** → rejected: they share the lifecycle of all channel media, which the existing `MESSAGE_MEDIA` purpose already fits.
