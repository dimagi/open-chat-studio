# ADR-0006: Combine email reply text and attachments into a single message

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

## Context

When a pipeline produces both a text response and file attachments (e.g. via `add_file_attachment()` in a Python node), the email channel must deliver them. The outbound pipeline (`ResponseSendingStage`) calls `sender.send_text()` then `sender.send_file()` once per file — a model that suits chat platforms where each call is an independent message. For email, emitting the text and each file as separate messages would spam the recipient's inbox and break threading.

Most senders are stateless and send immediately on each call. Email is different: it needs to accumulate the body and all attachments and emit a single message at the end of the turn.

## Decision

We will deliver the bot's text and all attachments as a single Django `EmailMessage`. To do this generically, we add a `flush()` lifecycle hook to `ChannelSender` with a no-op default; `ResponseSendingStage` calls `ctx.sender.flush()` once after all text/voice/file sends, inside the existing try block so failures flow through the same delivery-failure path. `EmailSender` becomes stateful: `send_text` buffers the body, `send_file` buffers `(filename, bytes, mimetype)`, and `flush` builds one `EmailMessage` with body, attachments, and threading headers, sends it, then resets. `flush` early-returns when nothing was buffered, so no empty email is sent. Outbound files are gated by `can_send_on_email`, trusting the File's already-sniffed `content_type` rather than re-reading bytes.

## Consequences

- One coherent, correctly threaded email per bot turn — body and attachments together.
- The `flush()` hook is generic; other buffering channels can adopt it, and stateless senders are unaffected by the no-op default.
- Failures in `flush()` (SMTP/SES errors) propagate through the existing `MessageDeliveryFailure` path — no special-casing.
- **Negative:** `EmailSender` now holds per-turn state; it must be reset after each flush and is not safe to share across turns concurrently.
- **Negative:** `send_file` reads full file bytes into memory before flush, raising peak memory during a send.

## Alternatives considered

- **Text reply plus one follow-up email per file:** rejected — inbox spam and broken threading.
- **Special-case email inside `ResponseSendingStage`:** rejected — leaks channel specifics into a generic stage; a sender-level `flush()` hook keeps the stage channel-agnostic.
- **Re-validate outbound bytes with magic at send time:** rejected — the type was already sniffed at `File.create`; re-reading trusted bot output is wasteful.
