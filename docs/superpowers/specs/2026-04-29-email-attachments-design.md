---
status: extracted
---

# Email Channel Attachments — Design

**Issue:** [#3246](https://github.com/dimagi/open-chat-studio/issues/3246) — Phase 2 of the email channel: bidirectional attachment support.

This design covered inbound attachment persistence and exposure to the LLM, content-type validation, and combining the bot's text reply with outbound attachments into a single email. The decisions have been crystallised into ADRs; see below.

## Decisions

- [ADR-0004](../../adr/0004-persist-inbound-email-attachments-in-handler.md) — Persist inbound email attachments in the webhook handler (full router; File IDs through Celery, not blobs; generic `attachment_file_ids` + `AttachmentHydrationStage`).
- [ADR-0005](../../adr/0005-validate-inbound-email-attachments-by-content.md) — Validate inbound email attachments by content sniffing (`python-magic` + defense-in-depth denylist; rejections surfaced as `message_text` metadata, no bounce).
- [ADR-0006](../../adr/0006-combine-email-reply-text-and-attachments.md) — Combine email reply text and attachments into a single message via a `ChannelSender.flush()` hook.
