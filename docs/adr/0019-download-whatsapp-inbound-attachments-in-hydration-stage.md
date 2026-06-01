# ADR-0019: Download WhatsApp inbound attachments in an overridden hydration stage

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

<p class="adr-meta">Extends: <a href="0004-persist-inbound-email-attachments-in-handler.md">ADR-0004</a></p>

## Context

ADR-0004 established a two-part pattern for inbound channel attachments: persist the bytes synchronously in the webhook handler, then run a generic `AttachmentHydrationStage` after session resolution to turn pre-persisted File IDs into `Attachment` objects with working session-scoped download links. That shape works for email because the SES webhook delivers the raw bytes in the request body — they are already in memory by the time the handler runs.

WhatsApp does not work that way. Twilio, Turn.io, and the Meta Cloud API all deliver inbound media as a *reference* (a `media_id` or a `media_url`), not as bytes. Fetching the image requires a second authenticated HTTP call to the provider. Doing that download in the webhook handler would block the `200` to the provider on a remote round-trip and would risk persisting bytes for messages that ultimately route to no channel. Doing it in the Celery task is fine — team context is already established and routing has already succeeded — but we still want to reuse the ChatAttachment linkage and `Attachment` construction that the generic hydration stage performs.

## Decision

We will download and persist inbound WhatsApp media inside the pipeline by subclassing `AttachmentHydrationStage`. `WhatsappAttachmentHydrationStage` overrides the `_get_files()` hook to fetch the bytes from the messaging service and persist them as a `MESSAGE_MEDIA` `File`. The base class then handles `ChatAttachment` linkage and `Attachment` construction unchanged. `WhatsappChannel` opts in by setting `attachment_hydration_stage_class = WhatsappAttachmentHydrationStage`; the base `ChannelBase._build_pipeline()` instantiates whatever class the subclass declares.

## Consequences

- Webhook handlers stay fast — no remote media download blocks the ack to Twilio/Turn.io/Meta.
- No orphaned files: download happens after session resolution, so a message that never reaches a session never persists media.
- The `_get_files()` hook is the documented extension point for any future channel whose media arrives by reference rather than by value (Telegram, Slack files, etc.) — same pattern, different acquisition code.
- The base class's ChatAttachment linkage and `Attachment` construction is reused; only the file-acquisition step is channel-specific.
- **Negative:** the two channels now follow visibly different persistence shapes — email persists in the handler, WhatsApp persists in the stage — which adds a branch to the mental model even though they share the post-persistence plumbing.

## Alternatives considered

- **Download in the webhook handler (mirror ADR-0004 exactly):** rejected — blocks the provider ack on a remote download and risks orphaned files when routing later fails.
- **Download in the Celery task before pipeline entry:** rejected — duplicates the ChatAttachment/Attachment wiring the hydration stage already owns and bypasses the `attachment_file_ids` → `Attachment` contract the rest of the pipeline expects.
