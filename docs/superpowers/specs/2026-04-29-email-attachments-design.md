# Email Channel Attachments — Design

**Issue:** [#3246](https://github.com/dimagi/open-chat-studio/issues/3246) — Phase 2 of the email channel: bidirectional attachment support.

## Goals

- **Inbound:** when a user emails an attachment to the bot, save it as a `File`, expose it on the message so the LLM/pipeline can use it.
- **Outbound:** when the pipeline produces files (e.g. via `add_file_attachment()` in a Python node), include them as MIME attachments on the same email reply that carries the bot's text response.

## Non-Goals

- New `FilePurpose`. Reuse `MESSAGE_MEDIA`.
- Custom retention/expiry rules for email attachments — they follow the same lifecycle as any other channel media.
- Reply-to-bounce on rejected attachments. Rejections are surfaced via metadata in the user's message text so the LLM can mention them; no out-of-band bounce mail.
- Inline image extraction. Email "inline" parts (`Content-Disposition: inline`, embedded in HTML body) are skipped; only true attachments are processed. `AnymailInboundMessage.attachments` already excludes inlines.

## Decisions Made

| # | Question | Decision |
|---|---|---|
| 1 | Outbound shape — one combined email or text + per-file follow-ups? | **One combined email**: text body + all MIME attachments in a single Django `EmailMessage`. |
| 2 | Per-attachment size cap (inbound + outbound) | **20 MB**. |
| 3 | Behavior on oversized inbound attachment | **Surface as metadata in `message_text`** so the LLM can tell the user. No bounce reply. |
| 4 | Inbound MIME policy | **Denylist of executables / installers / disk images.** All other types accepted. |
| 5 | `FilePurpose` for inbound attachments | **`MESSAGE_MEDIA`** — same as voice notes and other channel media. |
| 6 | Outbound MIME policy | **Symmetric** with inbound denylist + 20 MB cap. Files that fail get inline download URLs (existing fallback). |
| 7 | Where do inbound files get saved? Webhook handler vs Celery task? | **Webhook handler.** Promotes the handler from "best-effort pre-filter" to full router; `File.create()` runs synchronously with team known. Celery payload carries `File` IDs only — never raw bytes. Avoids putting blobs through the Redis broker. |
| 8 | Type validation strategy | **Use `python-magic` to detect the canonical content type from bytes**, not the email-header content type or filename extension (both spoofable). Defense in depth: filename ext, header type, and magic-detected type are *all* checked against the denylist; ANY hit rejects. |

## Architecture

```
INBOUND
  AWS SES → anymail webhook → email_inbound_handler
        ├─ EmailMessage.parse(inbound) → headers, body, raw attachment list (in-memory)
        ├─ get_email_experiment_channel(...) → (channel, session)
        │     no match → return; no Celery enqueue, no files saved (no orphans)
        ├─ set_current_team(channel.team)
        ├─ for each raw attachment:
        │     - detect type with python-magic
        │     - reject if size > 20 MB or denylisted (ext / claimed type / detected type)
        │     - else File.create(team_id=channel.team_id, purpose=MESSAGE_MEDIA,
        │                        content_type=<magic-detected>)
        ├─ append "[Attachment X skipped — reason]" notes to email.message_text
        └─ handle_email_message.delay(
              email_data={..., attachment_file_ids:[...], skipped_attachments:[...]},
              channel_id=channel.id,
              session_id=session.id or None,
           )

CELERY TASK
  handle_email_message
        ├─ EmailMessage(**email_data)
        ├─ Reload channel + session by ID (already routed; trust the IDs)
        └─ EmailChannel.new_user_message(message)
              ├─ ParticipantValidationStage
              ├─ SessionResolutionStage           (creates session if needed)
              ├─ SessionActivationStage
              ├─ AttachmentHydrationStage         ← NEW (generic); file IDs → Attachment objects
              ├─ MessageTypeValidationStage
              ├─ QueryExtractionStage
              ├─ ChatMessageCreationStage         (now sees message.attachments)
              ├─ BotInteractionStage              (passes attachments to bot)
              └─ ResponseFormattingStage

OUTBOUND
  ResponseSendingStage (modified):
        ├─ ctx.sender.send_text(formatted_message, recipient)
        ├─ for file in ctx.files_to_send:
        │     ctx.sender.send_file(file, recipient, session_id)
        └─ ctx.sender.flush()                       ← NEW; ChannelSender base = no-op

  EmailSender becomes stateful:
        send_text → buffers body
        send_file → buffers (filename, bytes, mime)
        flush    → builds one Django EmailMessage with body + attachments + threading
                   headers; sends; resets state.
```

## Components

### Data model — `apps/channels/datamodels.py`

No DB changes. One new field on `BaseMessage`, plus email-specific fields and a transient attribute on `EmailMessage`:

```python
from pydantic import PrivateAttr


class BaseMessage(BaseModel):
    # existing fields...
    attachments: list[Attachment] = Field(default=[])
    attachment_file_ids: list[int] = Field(default=[])
    """File IDs for attachments persisted by the channel's inbound handler.
    Hydrated into `attachments` by AttachmentHydrationStage once a session
    exists. Channels that don't pre-persist files leave this empty."""


class SkippedAttachment(BaseModel):
    name: str
    reason: str   # human-readable; included verbatim in message_text note
    size: int     # bytes; 0 if unknown


class EmailMessage(BaseMessage):
    # existing email-specific fields...
    skipped_attachments: list[SkippedAttachment] = Field(default=[])

    # transient — populated by parse(), consumed by handler; never serialized
    _raw_attachments: list["RawAttachment"] = PrivateAttr(default_factory=list)
```

`attachment_file_ids` is generic — channels that v2-migrate after this work and want to pre-persist inbound files can populate it without further plumbing. `skipped_attachments` stays on `EmailMessage` because the "rejected for size/type" concept is currently specific to the email channel's intake rules.

`RawAttachment` is a private `@dataclass` defined in the same module — name, content_type, content_bytes — used only in-process between `parse()` and `_persist_inbound_attachments()`. Using `PrivateAttr` so pydantic v2 ignores it during dump/load (it would be dropped from a leading-underscore class field anyway, but `PrivateAttr` makes the intent explicit and gives a per-instance default).

### `EmailMessage.parse(inbound)` — extended

Iterate `inbound.attachments` (already excludes inlines). For each part, capture `get_filename()`, `get_content_type()` (split off any params), and `get_content_bytes()`. Set `_raw_attachments` on the returned message. The pydantic-visible fields (`attachment_file_ids`, `skipped_attachments`) are populated later by the handler.

### `email_inbound_handler` — promoted to full router

Order of operations:

1. Spam check (existing).
2. `EmailMessageDatamodel.parse(message)`.
3. `get_email_experiment_channel(...)` — full routing using the same priority chain that previously lived in the Celery task. No match → return.
4. `set_current_team(channel.team)`.
5. `_persist_inbound_attachments(email_msg._raw_attachments, team_id=channel.team_id)` → `(accepted_ids, skipped_attachments)`.
6. Set `email_msg.attachment_file_ids`, `email_msg.skipped_attachments`.
7. Augment `email_msg.message_text` with one bracketed line per skipped attachment, e.g. `[Attachment 'macro.exe' (1.2 MB) skipped — file type not allowed (detected: application/x-msdownload)]`.
8. `handle_email_message.delay(email_data=email_msg.model_dump(), channel_id=channel.id, session_id=session.id if session else None)`.

If routing fails at step 3, no files are saved — there are no orphans to clean up. If `_persist_inbound_attachments` raises mid-loop, individual attachment failures are caught per-iteration (skipped with reason `"storage error"`); a top-level failure is logged and the email proceeds with whatever was already saved.

### Shared constants — `apps/service_providers/file_limits.py`

The denylist constants and the email size cap live here so that both inbound persistence (in `email_channel.py`) and outbound sendability (`can_send_on_email`, in this same module) import from a single source. `file_limits.py` is already the canonical module for per-channel limits and has no dependencies on the channels app, so importing it from `email_channel.py` is a clean direction:

```python
EMAIL_MAX_ATTACHMENT_BYTES = 20 * MB

EMAIL_BLOCKED_EXTENSIONS: frozenset[str] = frozenset({
    "exe", "bat", "cmd", "com", "scr", "ps1", "vbs", "vbe", "wsf",
    "msi", "app", "dmg", "jar", "appimage", "deb", "rpm",
    "iso", "img",
})

EMAIL_BLOCKED_CONTENT_TYPES: frozenset[str] = frozenset({
    "application/x-msdownload",
    "application/x-msdos-program",
    "application/x-bat",
    "application/x-sh",
    "application/x-executable",
    "application/x-mach-binary",
    "application/x-elf",
    "application/x-iso9660-image",
    "application/x-apple-diskimage",
    "application/vnd.debian.binary-package",
    "application/x-rpm",
    "application/x-msi",
    "application/java-archive",
})
```

### `_persist_inbound_attachments` — new helper in `apps/channels/channels_v2/email_channel.py`

```python
import magic
from io import BytesIO
import pathlib

from apps.service_providers.file_limits import (
    EMAIL_MAX_ATTACHMENT_BYTES,
    EMAIL_BLOCKED_EXTENSIONS,
    EMAIL_BLOCKED_CONTENT_TYPES,
)


def _detect_content_type(content: bytes, fallback: str = "") -> str:
    try:
        detected = magic.from_buffer(content[:2048], mime=True)
        if detected and detected != "application/octet-stream":
            return detected
    except Exception:
        logger.exception("magic content-type detection failed")
    return fallback or "application/octet-stream"


def _is_blocked(extension: str, claimed_type: str, detected_type: str) -> str | None:
    """Returns a rejection reason if blocked, else None."""
    if extension in EMAIL_BLOCKED_EXTENSIONS:
        return f"file extension '.{extension}' not allowed"
    if detected_type in EMAIL_BLOCKED_CONTENT_TYPES:
        return f"file type not allowed (detected: {detected_type})"
    if claimed_type in EMAIL_BLOCKED_CONTENT_TYPES:
        return f"file type not allowed (claimed: {claimed_type})"
    return None


def _persist_inbound_attachments(
    raw: list[RawAttachment], team_id: int
) -> tuple[list[int], list[dict]]:
    accepted_ids: list[int] = []
    skipped: list[dict] = []
    for att in raw:
        size = len(att.content_bytes)
        ext = pathlib.Path(att.filename or "").suffix.lstrip(".").lower()
        detected = _detect_content_type(att.content_bytes, fallback=att.content_type)

        if reason := _is_blocked(ext, att.content_type, detected):
            skipped.append({"name": att.filename, "reason": reason, "size": size})
            continue
        if size > EMAIL_MAX_ATTACHMENT_BYTES:
            skipped.append({"name": att.filename, "reason": "exceeds 20 MB limit", "size": size})
            continue

        try:
            f = File.create(
                filename=att.filename or "attachment",
                file_obj=BytesIO(att.content_bytes),
                team_id=team_id,
                purpose=FilePurpose.MESSAGE_MEDIA,
                content_type=detected,
            )
        except Exception:
            logger.exception("Failed to persist inbound email attachment %r", att.filename)
            skipped.append({"name": att.filename, "reason": "storage error", "size": size})
            continue
        accepted_ids.append(f.id)

    return accepted_ids, skipped
```


### `handle_email_message` task — simplified

Channel/session lookup becomes a trusted ID lookup (routing already happened in the handler). The task does **not** hydrate `Attachment` objects — that happens later in the pipeline once a session exists, so the `download_link` cached on each `Attachment` always references a real session:

```python
@shared_task(...)
def handle_email_message(self, email_data: dict, channel_id: int, session_id: int | None = None):
    message = EmailMessageDatamodel(**email_data)
    channel = ExperimentChannel.objects.select_related("experiment", "team").get(id=channel_id)
    session = (
        ExperimentSession.objects.select_related("team", "participant", "experiment_channel")
        .get(id=session_id)
        if session_id else None
    )

    set_current_team(channel.team)

    thread_context = EmailThreadContext.from_inbound(message)
    handler = EmailChannel(
        experiment=channel.experiment.default_version,
        experiment_channel=channel,
        experiment_session=session,
        thread_context=thread_context,
    )
    update_taskbadger_data(self, handler, message)
    handler.new_user_message(message)
```

Backwards compat: `channel_id` is a new required arg. Existing in-flight tasks (queued before deploy) won't have it. The task signature accepts the legacy form (no `channel_id`) and falls back to the old routing logic for one release cycle, after which the fallback is removed.

### `AttachmentHydrationStage` — new pipeline stage in core

Generic stage that hydrates `BaseMessage.attachment_file_ids` into `BaseMessage.attachments`. Lives in `apps/channels/channels_v2/stages/core.py` and is wired into the default pipeline in `ChannelBase._build_pipeline()` between `SessionActivationStage` and `MessageTypeValidationStage`. Runs after session resolution (so `ctx.experiment_session` exists) and before `ChatMessageCreationStage` (which consumes `message.attachments`).

```python
class AttachmentHydrationStage(ProcessingStage):
    """Hydrate Attachment objects from file IDs once a session exists.

    Channels that pre-persist inbound files in their webhook handler
    (e.g. EmailChannel) populate ctx.message.attachment_file_ids; this
    stage converts those IDs into Attachment objects with download_links
    that reference a real session. No-op for channels that don't use
    this pattern.
    """

    def should_run(self, ctx) -> bool:
        return bool(
            ctx.message
            and getattr(ctx.message, "attachment_file_ids", None)
            and not ctx.message.attachments
            and ctx.experiment_session is not None
        )

    def process(self, ctx) -> None:
        files = File.objects.filter(
            id__in=ctx.message.attachment_file_ids,
            team_id=ctx.experiment.team_id,
        )
        ctx.message.attachments = [
            Attachment.from_file(f, type="ocs_attachments",
                                 session_id=ctx.experiment_session.id)
            for f in files
        ]
```

`EmailChannel` does not override `_build_pipeline()` — it inherits the default which now includes this stage.

### `ChannelSender.flush()` — new no-op default

```python
class ChannelSender:
    def send_text(...): raise NotImplementedError
    def send_voice(...): raise NotImplementedError
    def send_file(...): raise NotImplementedError
    def flush(self) -> None:
        """Override in senders that buffer. Called by ResponseSendingStage after all sends."""
        return None
```

### `ResponseSendingStage.process()` — call flush

One line added inside the existing `try`, after the per-file loop. Failures from `flush()` propagate into the same `MessageDeliveryFailure` notification path.

### `EmailSender` — stateful, sends on flush

```python
class EmailSender(ChannelSender):
    def __init__(self, from_address, domain, thread_context=None):
        self.from_address = from_address
        self.domain = domain
        self.thread_context = thread_context or EmailThreadContext()
        self.last_message_id: str | None = None
        self._body: str | None = None
        self._recipient: str | None = None
        self._attachments: list[tuple[str, bytes, str]] = []

    def send_text(self, text, recipient):
        self._body = text
        self._recipient = recipient

    def send_file(self, file, recipient, session_id):
        if self._recipient is None:
            self._recipient = recipient
        # File.file is a FieldFile; read full bytes (already gated by 20MB cap upstream)
        with file.file.open("rb") as fh:
            content = fh.read()
        self._attachments.append((
            file.name,
            content,
            file.content_type or "application/octet-stream",
        ))

    def flush(self):
        if self._body is None and not self._attachments:
            return
        ctx = self.thread_context
        msg = DjangoEmailMessage(
            subject=ctx.subject or "New message",
            body=self._body or "",
            from_email=self.from_address,
            to=[self._recipient],
        )
        msg_id = make_msgid(domain=self.domain)
        msg.extra_headers = {"Message-ID": msg_id}
        if ctx.in_reply_to:
            msg.extra_headers["In-Reply-To"] = ctx.in_reply_to
            msg.extra_headers["References"] = " ".join(ctx.references)
        for filename, content, mimetype in self._attachments:
            msg.attach(filename, content, mimetype)
        msg.send()
        self.last_message_id = msg_id
        # reset for any subsequent send (ad hoc messages, etc.)
        self._body = None
        self._recipient = None
        self._attachments = []
```

### `EmailChannel._get_capabilities()` + `_can_send_file`

```python
def _get_capabilities(self):
    return ChannelCapabilities(
        supports_voice_replies=False,
        supports_files=True,                     # was False
        supports_conversational_consent=False,
        supports_static_triggers=True,
        supported_message_types=self.supported_message_types,
        can_send_file=self._can_send_file,
    )

def _can_send_file(self, file) -> bool:
    return can_send_on_email(file.content_type, file.content_size).supported
```

### `apps/service_providers/file_limits.py` — `can_send_on_email`

```python
def can_send_on_email(content_type: str, content_size: int) -> SendabilityResult:
    """Email: 20 MB cap, executable/installer denylist applies."""
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not content_size or content_size <= 0:
        return SendabilityResult(False, "File size unknown")
    if content_type in EMAIL_BLOCKED_CONTENT_TYPES:
        return SendabilityResult(False, f"File type '{content_type}' not allowed for email")
    if content_size > EMAIL_MAX_ATTACHMENT_BYTES:
        return SendabilityResult(False, "Exceeds 20MB email attachment limit")
    return SendabilityResult(True, "")

FILE_SENDABILITY_CHECKERS["email"] = can_send_on_email
```

The inbound persistence checks the filename extension, claimed header type, and magic-detected type — all against the shared `EMAIL_BLOCKED_*` constants. Outbound trusts `file.content_type` (and `file.content_size`) because they were already magic-detected and sized at `File.create` time, and re-reading 20 MB of bytes just to re-validate would be wasteful for trusted bot-generated content.

## Failure Behavior

| Failure | Behavior |
|---|---|
| `EmailMessage.parse()` raises | Log and return; no Celery enqueue. (existing) |
| Routing returns no channel | Return; no files persisted. |
| Single attachment fails `File.create` | Caught per attachment; recorded as skipped with reason `"storage error"`; other attachments continue. |
| `_persist_inbound_attachments` raises at top level | Logged; email proceeds with no attachments rather than dropping the message. |
| Celery enqueue fails after files are saved | Existing retry policy on `handle_email_message` triggers retry. Files are reused via IDs in the payload. |
| Blocked or oversized inbound attachment | Not saved; reason surfaced in `message_text`. |
| Outbound: `send_file` reads missing bytes | Raises during `file.file.open()` → wrapped by terminal stage as `FileDeliveryFailure`; download-link fallback in body. |
| Outbound: `flush()` raises (SMTP/SES error) | Inside terminal `try` → recorded as `MessageDeliveryFailure`; existing notification path. |
| Outbound: nothing was queued | `flush()` early-returns; no empty email sent. |

## Testing

All in `apps/channels/tests/test_email_channel.py` unless noted.

**`TestEmailMessageParse` (extend)**
- `test_parse_extracts_attachments`
- `test_parse_excludes_inlines`
- `test_parse_no_attachments`

**`TestPersistInboundAttachments` (new, `@pytest.mark.django_db`)**
- `test_accepts_normal_file`
- `test_rejects_oversized`
- `test_rejects_denylisted_extension`
- `test_rejects_denylisted_content_type`
- `test_rejects_when_magic_detects_executable_with_innocent_filename` — bytes are an ELF binary but filename is `report.pdf` and claimed type is `application/pdf`; rejected on detected type.
- `test_canonical_content_type_is_magic_detected` — accepted file's saved `content_type` matches magic, not the (lying) header.
- `test_storage_error_isolated` — patch `File.create` to raise on the second of three attachments; first and third saved, second skipped.

**`TestEmailInboundHandler` (extend)**
- `test_handler_persists_files_before_enqueue`
- `test_handler_no_files_saved_when_no_channel_match`
- `test_skipped_attachments_appended_to_message_text`

**`TestHandleEmailMessageTask` (extend)**
- `test_task_uses_channel_id_when_provided`
- `test_task_legacy_payload_falls_back_to_routing` — covers the one-release-cycle fallback path.

**`TestAttachmentHydrationStage` (new, `apps/channels/tests/channels/stages/test_attachment_hydration.py`)**
- `test_hydrates_attachments_from_file_ids` — `attachment_file_ids` set, session exists; after stage runs `message.attachments` is populated and each carries a download_link with the real session id.
- `test_skips_when_no_file_ids` — `should_run` returns False for a `BaseMessage` without `attachment_file_ids`; no-op for non-email channels.
- `test_skips_when_session_missing` — `should_run` returns False; no DB query.
- `test_skips_when_attachments_already_populated` — idempotent; doesn't double-hydrate.

**`TestEmailSender` (extend)**
- `test_send_text_alone_sends_one_email`
- `test_send_text_then_files_sends_one_combined_email` — assert one `mail.outbox` entry with body, two attachments, threading headers.
- `test_flush_with_nothing_queued_is_noop`
- `test_flush_failure_propagates_via_response_sending_stage`

**`TestEmailChannel` (extend)**
- `test_capabilities_supports_files` — `supports_files is True`; `can_send_file` returns True for normal small file, False for 25 MB or `.exe`.

**`TestCanSendOnEmail` (new, `apps/service_providers/tests/test_file_limits.py`)**
- mirrors existing whatsapp/telegram/slack checker tests: under-limit, over-limit, denylisted, missing type/size.

**`test_response_sending.py` (extend)**
- `test_response_sending_stage_calls_flush`
- `test_flush_failure_recorded_as_message_delivery_failure`

## Files Changed

| File | Change |
|---|---|
| `apps/channels/datamodels.py` | Add `attachment_file_ids` field to `BaseMessage`; add `SkippedAttachment`, `RawAttachment`, new fields on `EmailMessage`; extend `EmailMessage.parse()` to capture raw attachments. |
| `apps/channels/channels_v2/stages/core.py` | Add generic `AttachmentHydrationStage`. |
| `apps/channels/channels_v2/channel_base.py` | Insert `AttachmentHydrationStage` into the default pipeline. |
| `apps/channels/channels_v2/email_channel.py` | Promote `email_inbound_handler` to full router; add `_persist_inbound_attachments`; make `EmailSender` stateful with `flush()`; flip `supports_files=True` and wire `can_send_file`. |
| `apps/channels/channels_v2/sender.py` | Add `ChannelSender.flush()` no-op default. |
| `apps/channels/channels_v2/stages/terminal.py` | `ResponseSendingStage.process()` calls `ctx.sender.flush()` after the file loop, inside the existing try block. |
| `apps/channels/tasks.py` | `handle_email_message` accepts `channel_id`/`session_id` and uses them directly (no re-routing). Legacy fallback path retained for one release cycle. Attachment hydration is deferred to the pipeline stage. |
| `apps/service_providers/file_limits.py` | Add `EMAIL_MAX_ATTACHMENT_BYTES`, `EMAIL_BLOCKED_EXTENSIONS`, `EMAIL_BLOCKED_CONTENT_TYPES`, `can_send_on_email`; register in `FILE_SENDABILITY_CHECKERS`. |
| `apps/channels/tests/test_email_channel.py` | New + extended test classes (see Testing). |
| `apps/service_providers/tests/test_file_limits.py` | New `TestCanSendOnEmail`. |
| `apps/channels/tests/channels/stages/test_response_sending.py` | Tests for flush wiring. |
