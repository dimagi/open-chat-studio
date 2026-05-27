# ADR-0004: Persist inbound email attachments in the webhook handler

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

<p class="adr-meta">Extends: <a href="0001-use-anymail-webhook-for-email-ingress.md">ADR-0001</a>, <a href="0002-email-channel-slack-style-routing.md">ADR-0002</a></p>

## Context

Phase 2 adds bidirectional attachment support to the email channel. Inbound email arrives via the anymail webhook handler (ADR-0001) and is routed through the Slack-style priority chain (ADR-0002). Before this change, that routing and the heavy work lived in a Celery task while the webhook handler was a best-effort pre-filter.

Attachments force a question: where do the bytes get persisted? Persisting in the Celery task means raw blobs travel through the Redis broker, team context must be re-derived, and files could be saved for emails that ultimately route to no channel. The persisted records also need to reach the pipeline and the LLM as `Attachment` objects with working, session-scoped download links.

## Decision

We will persist inbound attachments synchronously in the webhook handler, promoting it from a best-effort pre-filter to the full router. The handler resolves channel and session, sets team context, and calls `File.create()` so the Celery payload carries only File IDs — never raw bytes. A generic `BaseMessage.attachment_file_ids` field carries those IDs, and a generic `AttachmentHydrationStage` — inserted into the default pipeline after session resolution and before chat-message creation — converts them into `Attachment` objects whose download links reference the real session.

## Consequences

- No blobs through the Celery broker; payloads stay small and team context is known at persistence time.
- No orphaned files — if routing finds no channel, nothing is saved.
- `attachment_file_ids` and `AttachmentHydrationStage` are channel-agnostic; any future channel that pre-persists inbound files reuses the same plumbing without new wiring.
- Hydrating after session resolution guarantees download links point at a real session, not a placeholder.
- **Negative:** the webhook handler now does routing and `File.create()` synchronously before returning `200` to the ESP, raising webhook latency and the cost of a slow save.
- **Negative:** `handle_email_message` gains `channel_id`/`session_id` arguments; in-flight pre-deploy tasks need a one-release-cycle legacy fallback to the old routing.

## Alternatives considered

- **Persist in the Celery task:** rejected — raw bytes through the Redis broker, team-context re-derivation, and orphaned files on no-match.
- **Email-specific hydration instead of a generic field + stage:** rejected — other v2-migrated channels will want the same pre-persist pattern; a generic mechanism avoids re-plumbing.
- **A new `FilePurpose` for email attachments:** rejected — they share the lifecycle of all channel media; the existing `MESSAGE_MEDIA` purpose already fits.
