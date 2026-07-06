# ADR-0006: Combine email reply text and attachments into a single message

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-05-26</p>

## Context

When a pipeline produces both a text response and file attachments (e.g. via `add_file_attachment()`), `ResponseSendingStage` calls `sender.send_text()` then `sender.send_file()` once per file. This suits chat platforms where each call is an independent message, but for email it would spam the recipient and break threading.

Most senders are stateless and send immediately on each call. Email must instead accumulate the body and all attachments and emit a single message at the end of the turn.

## Decision

We will deliver the bot's text and all attachments as one Django `EmailMessage`:

- Add a `flush()` lifecycle hook to `ChannelSender` with a no-op default. `ResponseSendingStage` calls `ctx.sender.flush()` after all sends, inside the existing try block so failures use the same delivery-failure path.
- `EmailSender` becomes stateful: `send_text` and `send_file` buffer, and `flush` builds one `EmailMessage` with body, attachments, and threading headers, sends it, then resets. `flush` early-returns when nothing was buffered.
- Gate outbound files by `can_send_on_email`, trusting the File's already-sniffed `content_type` rather than re-reading bytes.

## Consequences

- One correctly threaded email per bot turn — body and attachments together.
- The `flush()` hook is generic; other buffering channels can adopt it, and stateless senders are unaffected by the no-op default.
- Failures in `flush()` propagate through the existing `MessageDeliveryFailure` path.
- **Negative:** `EmailSender` holds per-turn state; it must be reset after each flush and is not safe to share across turns concurrently.
- **Negative:** `send_file` reads full file bytes into memory before flush, raising peak memory during a send.

## Alternatives considered

- **Text reply plus one follow-up email per file** → rejected: inbox spam and broken threading.
- **Special-case email inside `ResponseSendingStage`** → rejected: leaks channel specifics into a generic stage; a sender-level `flush()` hook keeps the stage channel-agnostic.
- **Re-validate outbound bytes with magic at send time** → rejected: the type was already sniffed at `File.create`; re-reading trusted bot output is wasteful.
