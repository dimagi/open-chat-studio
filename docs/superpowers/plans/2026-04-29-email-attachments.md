# Email Channel Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bidirectional attachment support to the email channel — inbound files saved as `File` records and exposed to the LLM, outbound files attached to the bot's email reply.

**Architecture:** Inbound attachments are persisted in the anymail webhook handler (no blob through Celery), surfaced via a new `BaseMessage.attachment_file_ids` field, and hydrated into `Attachment` objects by a new generic `AttachmentHydrationStage` after session resolution. Outbound combines text + attachments into a single Django `EmailMessage` via a new `ChannelSender.flush()` hook called by `ResponseSendingStage`. Type validation uses `python-magic` to defeat header/extension spoofing, with a curated allowlist for textual `application/*` types.

**Tech Stack:** Python 3.13, Django, Celery, pydantic v2, django-anymail, python-magic, pytest.

**Spec:** `docs/superpowers/specs/2026-04-29-email-attachments-design.md`

**Conventions:**
- Run all tests with `uv run pytest <path> -v`.
- Lint after each task: `uv run ruff check apps/ --fix && uv run ruff format apps/`.
- Type check after each task: `uv run ty check apps/channels/ apps/service_providers/`.
- Commits should be small, focused, and use conventional-commit style (e.g. `feat:`, `test:`, `refactor:`).

---

### Task 1: Shared email size + denylist constants in `file_limits.py`

**Files:**
- Modify: `apps/service_providers/file_limits.py`
- Test: `apps/service_providers/tests/test_file_limits.py`

- [ ] **Step 1: Write the failing test for `can_send_on_email`**

Append to `apps/service_providers/tests/test_file_limits.py` (under the existing class structure):

```python
from apps.service_providers.file_limits import (
    EMAIL_BLOCKED_CONTENT_TYPES,
    EMAIL_BLOCKED_EXTENSIONS,
    EMAIL_MAX_ATTACHMENT_BYTES,
    EMAIL_TEXT_LIKE_APPLICATION_TYPES,
    can_send_on_email,
)


class TestCanSendOnEmail:
    """Tests for email file limits (20MB cap, executable denylist)."""

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected_supported"),
        [
            ("application/pdf", 1 * MB, True),
            ("application/pdf", 20 * MB, True),  # exactly at limit
            ("application/pdf", 20 * MB + 1, False),
            ("image/png", 5 * MB, True),
            ("text/csv", 1024, True),
            # Denylisted types
            ("application/x-msdownload", 1024, False),
            ("application/x-sh", 1024, False),
            ("application/java-archive", 1024, False),
        ],
    )
    def test_mime_and_size_limits(self, content_type, content_size, expected_supported):
        result = can_send_on_email(content_type, content_size)
        assert isinstance(result, SendabilityResult)
        assert result.supported is expected_supported

    def test_unsupported_oversize_has_reason(self):
        result = can_send_on_email("application/pdf", 21 * MB)
        assert result.supported is False
        assert "20MB" in result.reason

    def test_unsupported_denylisted_has_reason(self):
        result = can_send_on_email("application/x-msdownload", 1024)
        assert result.supported is False
        assert "not allowed" in result.reason.lower()

    def test_supported_has_empty_reason(self):
        result = can_send_on_email("application/pdf", 1 * MB)
        assert result.supported is True
        assert result.reason == ""

    @pytest.mark.parametrize(
        ("content_type", "content_size"),
        [
            ("", 1024),
            ("application/pdf", 0),
            ("application/pdf", -1),
        ],
    )
    def test_unknown_type_or_size(self, content_type, content_size):
        result = can_send_on_email(content_type, content_size)
        assert result.supported is False

    def test_strips_charset_param(self):
        # text/csv with charset param should still be considered
        result = can_send_on_email("text/csv; charset=utf-8", 1024)
        assert result.supported is True

    def test_constants_exposed(self):
        assert EMAIL_MAX_ATTACHMENT_BYTES == 20 * MB
        assert "exe" in EMAIL_BLOCKED_EXTENSIONS
        assert "application/x-msdownload" in EMAIL_BLOCKED_CONTENT_TYPES
        assert "application/json" in EMAIL_TEXT_LIKE_APPLICATION_TYPES
        # Script types deliberately excluded from text-like allowlist
        assert "application/javascript" not in EMAIL_TEXT_LIKE_APPLICATION_TYPES
        assert "application/x-sh" not in EMAIL_TEXT_LIKE_APPLICATION_TYPES

    def test_registered_in_sendability_checkers(self):
        assert FILE_SENDABILITY_CHECKERS.get("email") is can_send_on_email
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest apps/service_providers/tests/test_file_limits.py::TestCanSendOnEmail -v
```

Expected: ImportError or AttributeError — `EMAIL_*` constants and `can_send_on_email` don't exist yet.

- [ ] **Step 3: Add constants and `can_send_on_email` to `file_limits.py`**

Append to `apps/service_providers/file_limits.py`:

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

# Application-namespaced types that are actually textual. Magic typically
# returns text/plain for these, so a text/* detection should not be flagged
# as a mismatch when the claimed type is one of these. Deliberately excludes
# script types (application/javascript, application/x-sh, ...) — those are
# textual but executable.
EMAIL_TEXT_LIKE_APPLICATION_TYPES: frozenset[str] = frozenset({
    "application/json",
    "application/ld+json",
    "application/manifest+json",
    "application/xml",
    "application/atom+xml",
    "application/rss+xml",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/x-toml",
    "application/x-ndjson",
})


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

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest apps/service_providers/tests/test_file_limits.py::TestCanSendOnEmail -v
```

Expected: all 13+ test cases PASS.

- [ ] **Step 5: Lint, format, type check**

```bash
uv run ruff check apps/service_providers/ --fix
uv run ruff format apps/service_providers/
uv run ty check apps/service_providers/
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add apps/service_providers/file_limits.py apps/service_providers/tests/test_file_limits.py
git commit -m "feat(email): add email file limits and denylist constants

Adds EMAIL_MAX_ATTACHMENT_BYTES (20 MB), EMAIL_BLOCKED_EXTENSIONS,
EMAIL_BLOCKED_CONTENT_TYPES, EMAIL_TEXT_LIKE_APPLICATION_TYPES, and
can_send_on_email() to file_limits.py. Registers email in
FILE_SENDABILITY_CHECKERS for the existing capability-checking flow."
```

---

### Task 2: `ChannelSender.flush()` hook

**Files:**
- Modify: `apps/channels/channels_v2/sender.py`
- Modify: `apps/channels/channels_v2/stages/terminal.py:79-97`
- Modify: `apps/channels/tests/channels/conftest.py:15-30` (StubSender)
- Test: `apps/channels/tests/channels/stages/test_response_sending.py`

- [ ] **Step 1: Write failing test for flush wiring**

Append to `apps/channels/tests/channels/stages/test_response_sending.py`:

```python
def test_response_sending_stage_calls_flush(self):
    sender = StubSender()
    ctx = make_context(
        sender=sender,
        formatted_message="Hello",
        participant_identifier="user1",
    )

    self.stage(ctx)

    assert sender.flush_call_count == 1

def test_flush_is_called_after_files(self):
    sender = StubSender()
    file1 = MagicMock()
    session = MagicMock()
    session.id = 42
    ctx = make_context(
        sender=sender,
        formatted_message="Hello",
        participant_identifier="user1",
        experiment_session=session,
        files_to_send=[file1],
    )

    self.stage(ctx)

    # flush must be called once, after both text and files were sent
    assert sender.flush_call_count == 1
    assert sender.call_order == ["send_text", "send_file", "flush"]

def test_flush_failure_recorded_as_message_delivery_failure(self):
    sender = StubSender()
    error = RuntimeError("flush failed")
    sender.flush_side_effect = error
    ctx = make_context(
        sender=sender,
        formatted_message="Hello",
        participant_identifier="user1",
    )

    self.stage(ctx)

    assert len(ctx.sending_exceptions) == 1
    assert ctx.sending_exceptions[0] is error or isinstance(ctx.sending_exceptions[0], RuntimeError)
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest apps/channels/tests/channels/stages/test_response_sending.py::TestResponseSendingStage::test_response_sending_stage_calls_flush -v
```

Expected: AttributeError on `sender.flush_call_count` — `StubSender` has no flush tracking.

- [ ] **Step 3: Add flush to ChannelSender base**

Modify `apps/channels/channels_v2/sender.py`:

```python
class ChannelSender:
    """Abstracts how a channel delivers messages to the user.

    Sender implementations encapsulate platform-specific sending details
    (e.g., from_number, bot token, thread_ts) at construction time.
    The send methods receive only the data that varies per call.

    Default implementations raise NotImplementedError. Channels only override
    the methods their capabilities support -- the capabilities layer gates which
    methods actually get called at runtime.
    """

    def send_text(self, text: str, recipient: str) -> None:
        raise NotImplementedError

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        raise NotImplementedError

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        raise NotImplementedError

    def flush(self) -> None:
        """Called by ResponseSendingStage after all text/voice/file sends.

        Default is no-op. Override in senders that buffer the response
        (e.g. EmailSender batching text + attachments into a single email).
        """
        return None
```

- [ ] **Step 4: Update StubSender to track flush**

Modify `apps/channels/tests/channels/conftest.py` `StubSender`:

```python
class StubSender(ChannelSender):
    """Captures outbound messages for assertions."""

    def __init__(self):
        self.text_messages = []
        self.voice_messages = []
        self.files_sent = []
        self.flush_call_count = 0
        self.flush_side_effect: Exception | None = None
        self.call_order: list[str] = []

    def send_text(self, text, recipient):
        self.text_messages.append((text, recipient))
        self.call_order.append("send_text")

    def send_voice(self, audio, recipient):
        self.voice_messages.append((audio, recipient))
        self.call_order.append("send_voice")

    def send_file(self, file, recipient, session_id):
        self.files_sent.append((file, recipient, session_id))
        self.call_order.append("send_file")

    def flush(self):
        self.flush_call_count += 1
        self.call_order.append("flush")
        if self.flush_side_effect:
            raise self.flush_side_effect
```

- [ ] **Step 5: Wire flush into ResponseSendingStage**

Modify `apps/channels/channels_v2/stages/terminal.py` `ResponseSendingStage.process()`. Find the existing method (around line 79-97) and add the `flush()` call inside the existing `try` block after the for-loop:

```python
    def process(self, ctx: MessageProcessingContext) -> None:
        try:
            if ctx.early_exit_response:
                self._send_text(ctx, ctx.early_exit_response, ctx.participant_identifier)
                ctx.sender.flush()
                return

            # Normal path -- send formatted bot response
            if ctx.voice_audio:
                self._send_voice(ctx, ctx.voice_audio, ctx.participant_identifier)
                if ctx.additional_text_message:
                    self._send_text(ctx, ctx.additional_text_message, ctx.participant_identifier)
            else:
                self._send_text(ctx, ctx.formatted_message, ctx.participant_identifier)

            for file in ctx.files_to_send:
                self._send_file(ctx, file, ctx.participant_identifier)

            ctx.sender.flush()
        except Exception as e:
            ctx.sending_exceptions.append(e)
            ctx.processing_errors.append(f"Send failed: {e}")
```

- [ ] **Step 6: Run test to verify pass**

```bash
uv run pytest apps/channels/tests/channels/stages/test_response_sending.py -v
```

Expected: all existing tests still pass, plus the three new flush tests.

- [ ] **Step 7: Run full channel test suite as smoke check**

```bash
uv run pytest apps/channels/tests/ -v --no-header -q 2>&1 | tail -30
```

Expected: no regressions. Existing senders inherit the no-op flush so nothing else needs touching.

- [ ] **Step 8: Lint, format, type check**

```bash
uv run ruff check apps/channels/ --fix
uv run ruff format apps/channels/
uv run ty check apps/channels/
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add apps/channels/channels_v2/sender.py apps/channels/channels_v2/stages/terminal.py apps/channels/tests/channels/conftest.py apps/channels/tests/channels/stages/test_response_sending.py
git commit -m "feat(channels): add ChannelSender.flush() hook

Adds a no-op flush() to ChannelSender called by ResponseSendingStage
after all text/voice/file sends. Channels that buffer (e.g. email
batching text + attachments into one message) override flush to
actually deliver. Failures inside flush propagate via the same
sending_exceptions path as other delivery failures."
```

---

### Task 3: `BaseMessage.attachment_file_ids` field

**Files:**
- Modify: `apps/channels/datamodels.py:83-91` (BaseMessage)
- Test: `apps/channels/tests/test_datamodels.py` (create if missing)

- [ ] **Step 1: Write failing test**

Create `apps/channels/tests/test_datamodels.py` (or append if exists):

```python
from apps.channels.datamodels import BaseMessage


class TestBaseMessage:
    def test_default_attachment_file_ids_empty(self):
        msg = BaseMessage(participant_id="u1", message_text="hi")
        assert msg.attachment_file_ids == []

    def test_attachment_file_ids_serialized(self):
        msg = BaseMessage(participant_id="u1", message_text="hi", attachment_file_ids=[1, 2, 3])
        dumped = msg.model_dump()
        assert dumped["attachment_file_ids"] == [1, 2, 3]

    def test_attachment_file_ids_round_trip(self):
        msg = BaseMessage(participant_id="u1", message_text="hi", attachment_file_ids=[42])
        rebuilt = BaseMessage(**msg.model_dump())
        assert rebuilt.attachment_file_ids == [42]
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest apps/channels/tests/test_datamodels.py::TestBaseMessage -v
```

Expected: ValidationError or attribute missing.

- [ ] **Step 3: Add field to BaseMessage**

Modify `apps/channels/datamodels.py` `BaseMessage`:

```python
class BaseMessage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    participant_id: str
    message_text: str
    content_type: MESSAGE_TYPES | None = Field(default=MESSAGE_TYPES.TEXT)
    attachments: list[Attachment] = Field(default=[])
    attachment_file_ids: list[int] = Field(default=[])
    """File IDs for attachments persisted by the channel's inbound handler.
    Hydrated into `attachments` by AttachmentHydrationStage once a session
    exists. Channels that don't pre-persist files leave this empty."""

    cached_media_data: MediaCache | None = Field(default=None, exclude=True)
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest apps/channels/tests/test_datamodels.py::TestBaseMessage -v
```

Expected: PASS.

- [ ] **Step 5: Confirm no regressions in existing channel tests**

```bash
uv run pytest apps/channels/tests/ -v --no-header -q 2>&1 | tail -20
```

Expected: no regressions.

- [ ] **Step 6: Lint, format, type check**

```bash
uv run ruff check apps/channels/ --fix
uv run ruff format apps/channels/
uv run ty check apps/channels/
```

- [ ] **Step 7: Commit**

```bash
git add apps/channels/datamodels.py apps/channels/tests/test_datamodels.py
git commit -m "feat(channels): add attachment_file_ids to BaseMessage

Generic field for channels that pre-persist inbound attachments in
their webhook handler. Hydrated into Attachment objects later by a
new pipeline stage once the session is known. Default empty list
keeps existing channels unaffected."
```

---

### Task 4: `AttachmentHydrationStage` in core stages

**Files:**
- Modify: `apps/channels/channels_v2/stages/core.py` (add new stage class)
- Modify: `apps/channels/channels_v2/channel_base.py:107-130` (insert into pipeline)
- Test: `apps/channels/tests/channels/stages/test_attachment_hydration.py` (create)

- [ ] **Step 1: Write failing tests**

Create `apps/channels/tests/channels/stages/test_attachment_hydration.py`:

```python
from unittest.mock import MagicMock

import pytest

from apps.channels.channels_v2.stages.core import AttachmentHydrationStage
from apps.channels.datamodels import Attachment, BaseMessage
from apps.channels.tests.channels.conftest import make_context
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.files import FileFactory


class TestAttachmentHydrationStage:
    def setup_method(self):
        self.stage = AttachmentHydrationStage()

    def test_skips_when_no_file_ids(self):
        msg = BaseMessage(participant_id="u1", message_text="hi")
        ctx = make_context(message=msg, experiment_session=MagicMock())
        assert self.stage.should_run(ctx) is False

    def test_skips_when_session_missing(self):
        msg = BaseMessage(participant_id="u1", message_text="hi", attachment_file_ids=[1])
        ctx = make_context(message=msg, experiment_session=None)
        assert self.stage.should_run(ctx) is False

    def test_skips_when_attachments_already_populated(self):
        msg = BaseMessage(
            participant_id="u1",
            message_text="hi",
            attachment_file_ids=[1],
            attachments=[Attachment(file_id=1, type="ocs_attachments", name="x", size=1, download_link="")],
        )
        ctx = make_context(message=msg, experiment_session=MagicMock())
        assert self.stage.should_run(ctx) is False

    @pytest.mark.django_db()
    def test_hydrates_attachments_from_file_ids(self):
        experiment = ExperimentFactory()
        session = ExperimentSessionFactory(experiment=experiment, team=experiment.team)
        file_a = FileFactory(team=experiment.team, name="a.pdf", content_type="application/pdf")
        file_b = FileFactory(team=experiment.team, name="b.csv", content_type="text/csv")

        msg = BaseMessage(
            participant_id="u1",
            message_text="hi",
            attachment_file_ids=[file_a.id, file_b.id],
        )
        ctx = make_context(
            message=msg,
            experiment=experiment,
            experiment_session=session,
        )

        assert self.stage.should_run(ctx) is True
        self.stage.process(ctx)

        assert len(msg.attachments) == 2
        names = {a.name for a in msg.attachments}
        assert names == {"a.pdf", "b.csv"}
        for att in msg.attachments:
            assert att.type == "ocs_attachments"
            # download_link must reference the real session, not 0
            assert str(session.id) in att.download_link
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest apps/channels/tests/channels/stages/test_attachment_hydration.py -v
```

Expected: ImportError on `AttachmentHydrationStage`.

- [ ] **Step 3: Add stage class to core.py**

Append to `apps/channels/channels_v2/stages/core.py`:

```python
class AttachmentHydrationStage(ProcessingStage):
    """Hydrate Attachment objects from file IDs once a session exists.

    Channels that pre-persist inbound files in their webhook handler
    (e.g. EmailChannel) populate ctx.message.attachment_file_ids; this
    stage converts those IDs into Attachment objects with download_links
    that reference a real session. No-op for channels that don't use
    this pattern.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return bool(
            ctx.message
            and getattr(ctx.message, "attachment_file_ids", None)
            and not ctx.message.attachments
            and ctx.experiment_session is not None
        )

    def process(self, ctx: MessageProcessingContext) -> None:
        files = File.objects.filter(
            id__in=ctx.message.attachment_file_ids,
            team_id=ctx.experiment.team_id,
        )
        ctx.message.attachments = [
            Attachment.from_file(f, type="ocs_attachments", session_id=ctx.experiment_session.id)
            for f in files
        ]
```

Confirm imports at the top of `core.py` include `File` and `Attachment`. If `Attachment` isn't already imported, add:

```python
from apps.channels.datamodels import Attachment
```

- [ ] **Step 4: Wire into the default pipeline**

Modify `apps/channels/channels_v2/channel_base.py` `_build_pipeline()` to insert the new stage between `SessionActivationStage` and `MessageTypeValidationStage`:

```python
    def _build_pipeline(self) -> MessageProcessingPipeline:
        """Build the default processing pipeline. Subclasses can override entirely.
        Core stages can be short-circuited by EarlyExitResponse.
        Terminal stages always run.
        """
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                SessionResolutionStage(),
                SessionActivationStage(),
                AttachmentHydrationStage(),
                MessageTypeValidationStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                ConsentFlowStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                ResponseSendingStage(),
                SendingErrorHandlerStage(),
                PersistenceStage(),
                ActivityTrackingStage(),
            ],
        )
```

Add `AttachmentHydrationStage` to the import block at the top of `channel_base.py`:

```python
from apps.channels.channels_v2.stages.core import (
    AttachmentHydrationStage,
    BotInteractionStage,
    ChatMessageCreationStage,
    ConsentFlowStage,
    MessageTypeValidationStage,
    ParticipantValidationStage,
    QueryExtractionStage,
    ResponseFormattingStage,
    SessionActivationStage,
    SessionResolutionStage,
)
```

- [ ] **Step 5: Run new tests**

```bash
uv run pytest apps/channels/tests/channels/stages/test_attachment_hydration.py -v
```

Expected: PASS.

- [ ] **Step 6: Run full channel test suite as smoke check**

```bash
uv run pytest apps/channels/tests/ -q 2>&1 | tail -20
```

Expected: no regressions. The new stage's `should_run` returns False whenever `attachment_file_ids` is empty (every existing channel), so it's an effective no-op.

- [ ] **Step 7: Lint, format, type check**

```bash
uv run ruff check apps/channels/ --fix
uv run ruff format apps/channels/
uv run ty check apps/channels/
```

- [ ] **Step 8: Commit**

```bash
git add apps/channels/channels_v2/stages/core.py apps/channels/channels_v2/channel_base.py apps/channels/tests/channels/stages/test_attachment_hydration.py
git commit -m "feat(channels): add AttachmentHydrationStage to core pipeline

Generic stage that converts BaseMessage.attachment_file_ids into
Attachment objects after the session is resolved, so download_links
on each Attachment reference a real session. Channels that don't
pre-persist inbound files (i.e. don't populate attachment_file_ids)
get a no-op via should_run."
```

---

### Task 5: `EmailMessage` datamodel — `SkippedAttachment`, `RawAttachment`, parse extraction

**Files:**
- Modify: `apps/channels/datamodels.py:276-303` (EmailMessage + parse)
- Modify: `apps/channels/tests/test_email_channel.py:89-141` (TestEmailMessageParse)

- [ ] **Step 1: Write failing tests**

Append to `apps/channels/tests/test_email_channel.py` `TestEmailMessageParse` class:

```python
def _make_inbound_with_attachments(self, parts):
    """Create a mock inbound where .attachments returns the given list of MIMEPart-like mocks."""
    msg = _make_inbound_message()
    msg.attachments = parts
    return msg

def _mime_part(filename="file.bin", content_type="application/octet-stream", content=b"bytes"):
    part = MagicMock()
    part.get_filename.return_value = filename
    part.get_content_type.return_value = content_type
    part.get_content_bytes.return_value = content
    return part

def test_parse_extracts_attachments(self):
    pdf = self._mime_part(filename="report.pdf", content_type="application/pdf", content=b"%PDF-")
    csv = self._mime_part(filename="data.csv", content_type="text/csv", content=b"a,b,c")
    inbound = self._make_inbound_with_attachments([pdf, csv])

    result = EmailMessage.parse(inbound)

    assert len(result._raw_attachments) == 2
    assert result._raw_attachments[0].filename == "report.pdf"
    assert result._raw_attachments[0].content_type == "application/pdf"
    assert result._raw_attachments[0].content_bytes == b"%PDF-"
    assert result._raw_attachments[1].filename == "data.csv"

def test_parse_no_attachments(self):
    inbound = self._make_inbound_with_attachments([])
    result = EmailMessage.parse(inbound)
    assert result._raw_attachments == []

def test_parse_strips_content_type_params(self):
    part = self._mime_part(content_type="text/csv; charset=utf-8")
    inbound = self._make_inbound_with_attachments([part])
    result = EmailMessage.parse(inbound)
    assert result._raw_attachments[0].content_type == "text/csv"

def test_parse_handles_missing_filename(self):
    part = self._mime_part(filename=None)
    inbound = self._make_inbound_with_attachments([part])
    result = EmailMessage.parse(inbound)
    assert result._raw_attachments[0].filename == "attachment"
```

Note: helpers `_make_inbound_with_attachments` / `_mime_part` should be defined as methods on the test class or as module-level helpers; the existing module already has `_make_inbound_message` at module level.

Make `_make_inbound_with_attachments` and `_mime_part` module-level helpers next to `_make_inbound_message`. The existing `_make_inbound_message` fixture mocks `msg.attachments` implicitly — set it explicitly:

```python
def _make_inbound_with_attachments(parts, **kwargs):
    msg = _make_inbound_message(**kwargs)
    msg.attachments = parts
    return msg


def _mime_part(filename="file.bin", content_type="application/octet-stream", content=b"bytes"):
    part = MagicMock()
    part.get_filename.return_value = filename
    part.get_content_type.return_value = content_type
    part.get_content_bytes.return_value = content
    return part
```

Then update the test methods to call them as module-level functions (no `self.`).

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailMessageParse -v
```

Expected: AttributeError on `result._raw_attachments` and/or test setup failures.

- [ ] **Step 3: Add `RawAttachment`, `SkippedAttachment`, extend `EmailMessage`**

Modify `apps/channels/datamodels.py` — add imports/classes near the top of the email section:

```python
from dataclasses import dataclass

from pydantic import PrivateAttr


@dataclass
class RawAttachment:
    """In-memory carrier for an inbound email attachment between parse() and
    the persistence helper. Never serialized; lives only in handler scope."""

    filename: str
    content_type: str
    content_bytes: bytes


class SkippedAttachment(BaseModel):
    """Inbound attachment that was rejected at intake. Reported to the LLM
    via injected notes in the user's message_text."""

    name: str
    reason: str
    size: int = 0


class EmailMessage(BaseMessage):
    """Inbound email parsed from AnymailInboundMessage."""

    from_address: str = Field(max_length=254)
    to_address: str = Field(max_length=254)
    subject: str = Field(max_length=1000)
    message_id: str = Field(max_length=500)
    in_reply_to: str | None = None
    references: list[str] = Field(default=[])
    skipped_attachments: list[SkippedAttachment] = Field(default=[])

    _raw_attachments: list[RawAttachment] = PrivateAttr(default_factory=list)

    @staticmethod
    def parse(inbound) -> "EmailMessage":
        body = inbound.text or ""
        reply = EmailReplyParser(languages=["en", "de", "fr", "es", "pt", "it", "nl", "pl", "sv", "da", "no"]).read(
            body
        )
        stripped_text = reply.latest_reply or body

        message = EmailMessage(
            participant_id=inbound.from_email.addr_spec,
            message_text=stripped_text,
            from_address=inbound.from_email.addr_spec,
            to_address=inbound.to[0].addr_spec if inbound.to else "",
            subject=inbound.subject or "",
            message_id=inbound.get("Message-ID", ""),
            in_reply_to=inbound.get("In-Reply-To"),
            references=_parse_references(inbound.get("References", "")),
        )
        message._raw_attachments = _extract_raw_attachments(inbound)
        return message


def _extract_raw_attachments(inbound) -> list[RawAttachment]:
    """Pull MIMEPart objects from the inbound message into in-memory RawAttachment records.
    AnymailInboundMessage.attachments already excludes inlines."""
    raw = []
    for part in getattr(inbound, "attachments", None) or []:
        try:
            content_type = (part.get_content_type() or "application/octet-stream").split(";")[0].strip().lower()
            raw.append(
                RawAttachment(
                    filename=part.get_filename() or "attachment",
                    content_type=content_type,
                    content_bytes=part.get_content_bytes(),
                )
            )
        except Exception:
            logger.exception("Failed to read inbound email attachment part")
    return raw
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailMessageParse -v
```

Expected: all parse tests pass (existing + 4 new).

- [ ] **Step 5: Lint, format, type check**

```bash
uv run ruff check apps/channels/ --fix
uv run ruff format apps/channels/
uv run ty check apps/channels/
```

- [ ] **Step 6: Commit**

```bash
git add apps/channels/datamodels.py apps/channels/tests/test_email_channel.py
git commit -m "feat(email): extract raw attachments in EmailMessage.parse

Adds RawAttachment dataclass and SkippedAttachment pydantic model;
extends EmailMessage with skipped_attachments field and a transient
_raw_attachments PrivateAttr populated by parse(). Captures the
filename, content type (with params stripped), and bytes for each
non-inline part — to be filtered and persisted by the handler."
```

---

### Task 6: `_persist_inbound_attachments` helper

**Files:**
- Modify: `apps/channels/channels_v2/email_channel.py` (add helpers + constants imports)
- Modify: `apps/channels/tests/test_email_channel.py` (new TestPersistInboundAttachments)

- [ ] **Step 1: Write failing tests**

Append to `apps/channels/tests/test_email_channel.py`:

```python
from io import BytesIO

from apps.channels.channels_v2.email_channel import (
    _is_blocked,
    _persist_inbound_attachments,
)
from apps.channels.datamodels import RawAttachment
from apps.files.models import File
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
class TestPersistInboundAttachments:
    def _raw(self, filename, content_type, content):
        return RawAttachment(filename=filename, content_type=content_type, content_bytes=content)

    def test_accepts_normal_file(self):
        team = TeamFactory()
        raw = [self._raw("data.csv", "text/csv", b"a,b\n1,2\n")]

        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert len(accepted) == 1
        assert skipped == []
        f = File.objects.get(id=accepted[0])
        assert f.team_id == team.id
        assert f.name == "data.csv"
        assert f.purpose == "message_media"

    def test_rejects_oversized(self):
        team = TeamFactory()
        big = b"x" * (21 * 1024 * 1024)
        raw = [self._raw("big.pdf", "application/pdf", big)]

        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert accepted == []
        assert len(skipped) == 1
        assert "20" in skipped[0]["reason"]
        assert skipped[0]["size"] == len(big)

    def test_rejects_denylisted_extension(self):
        team = TeamFactory()
        raw = [self._raw("malware.exe", "application/octet-stream", b"MZ\x90\x00")]

        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert accepted == []
        assert len(skipped) == 1
        assert ".exe" in skipped[0]["reason"]

    def test_rejects_denylisted_content_type(self):
        team = TeamFactory()
        raw = [self._raw("noext", "application/x-msdownload", b"\x00\x00")]

        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert accepted == []
        assert "application/x-msdownload" in skipped[0]["reason"]

    def test_rejects_when_magic_detects_executable_with_innocent_filename(self):
        # ELF magic bytes; filename + claimed type lie about the content
        team = TeamFactory()
        elf_bytes = b"\x7fELF\x02\x01\x01\x00" + (b"\x00" * 64)
        raw = [self._raw("report.pdf", "application/pdf", elf_bytes)]

        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert accepted == []
        assert "detected" in skipped[0]["reason"].lower()

    def test_canonical_content_type_is_magic_detected(self):
        team = TeamFactory()
        # PNG magic bytes; sender lies and claims text/plain
        png_bytes = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 32)
        raw = [self._raw("image.png", "image/png", png_bytes)]

        # text/plain mismatch would block, but png matches header so allowed
        accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert len(accepted) == 1
        f = File.objects.get(id=accepted[0])
        assert f.content_type.startswith("image/png")

    def test_storage_error_isolated(self):
        team = TeamFactory()
        raw = [
            self._raw("a.txt", "text/plain", b"hello"),
            self._raw("b.txt", "text/plain", b"world"),
            self._raw("c.txt", "text/plain", b"again"),
        ]
        original = File.create
        call_count = {"n": 0}

        def flaky(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated storage error")
            return original(*args, **kwargs)

        with patch.object(File, "create", side_effect=flaky):
            accepted, skipped = _persist_inbound_attachments(raw, team_id=team.id)

        assert len(accepted) == 2
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "storage error"
        assert skipped[0]["name"] == "b.txt"

    @pytest.mark.parametrize(
        ("ext", "claimed", "detected", "should_block"),
        [
            ("pdf", "image/jpeg", "application/pdf", True),  # cross-category mismatch
            ("pdf", "application/octet-stream", "application/pdf", False),  # claimed unknown
            ("pdf", "application/pdf", "application/octet-stream", False),  # detected unknown
            ("json", "application/json", "text/plain", False),  # text-like allowlist
            ("xml", "application/xml", "text/plain", False),
            ("csv", "text/csv", "application/javascript", True),  # script not allowlisted
            ("csv", "text/csv", "text/plain", False),  # same text category
        ],
    )
    def test_is_blocked_mismatch_matrix(self, ext, claimed, detected, should_block):
        result = _is_blocked(ext, claimed, detected)
        if should_block:
            assert result is not None
            assert "mismatch" in result.lower() or "not allowed" in result.lower()
        else:
            assert result is None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestPersistInboundAttachments -v
```

Expected: ImportError on `_persist_inbound_attachments` / `_is_blocked`.

- [ ] **Step 3: Add helpers to `email_channel.py`**

Modify `apps/channels/channels_v2/email_channel.py` — add imports near the top:

```python
import pathlib
from io import BytesIO

import magic

from apps.channels.datamodels import RawAttachment
from apps.files.models import File, FilePurpose
from apps.service_providers.file_limits import (
    EMAIL_BLOCKED_CONTENT_TYPES,
    EMAIL_BLOCKED_EXTENSIONS,
    EMAIL_MAX_ATTACHMENT_BYTES,
    EMAIL_TEXT_LIKE_APPLICATION_TYPES,
)
```

Add helpers below the existing module-level helpers (e.g. after `_domain_from_address`):

```python
def _detect_content_type(content: bytes, fallback: str = "") -> str:
    try:
        detected = magic.from_buffer(content[:2048], mime=True)
        if detected and detected != "application/octet-stream":
            return detected
    except Exception:
        logger.exception("magic content-type detection failed")
    return fallback or "application/octet-stream"


def _category(content_type: str) -> str:
    """Top-level category for mismatch comparison.
    Maps known textual application/* types (JSON, XML, YAML, ...) to 'text'
    since magic typically returns text/plain for them.
    """
    if content_type in EMAIL_TEXT_LIKE_APPLICATION_TYPES:
        return "text"
    return content_type.split("/", 1)[0]


def _is_blocked(extension: str, claimed_type: str, detected_type: str) -> str | None:
    """Returns a rejection reason if blocked, else None."""
    if extension in EMAIL_BLOCKED_EXTENSIONS:
        return f"file extension '.{extension}' not allowed"
    if detected_type in EMAIL_BLOCKED_CONTENT_TYPES:
        return f"file type not allowed (detected: {detected_type})"
    if claimed_type in EMAIL_BLOCKED_CONTENT_TYPES:
        return f"file type not allowed (claimed: {claimed_type})"
    if (
        claimed_type
        and claimed_type != "application/octet-stream"
        and detected_type != "application/octet-stream"
        and _category(claimed_type) != _category(detected_type)
    ):
        return f"content type mismatch (claimed: {claimed_type}, detected: {detected_type})"
    return None


def _persist_inbound_attachments(
    raw: list[RawAttachment], team_id: int
) -> tuple[list[int], list[dict]]:
    """Filter and save inbound email attachments, returning accepted File IDs
    and skipped-attachment metadata for surfacing to the LLM."""
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

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestPersistInboundAttachments -v
```

Expected: all tests pass.

- [ ] **Step 5: Lint, format, type check**

```bash
uv run ruff check apps/channels/ --fix
uv run ruff format apps/channels/
uv run ty check apps/channels/
```

- [ ] **Step 6: Commit**

```bash
git add apps/channels/channels_v2/email_channel.py apps/channels/tests/test_email_channel.py
git commit -m "feat(email): add attachment intake filtering and persistence

Adds _persist_inbound_attachments() with python-magic content-type
detection, extension/MIME denylist, cross-category mismatch detection,
and a 20 MB cap. Magic-detected type becomes the canonical
content_type stored on the File record. Per-attachment failures are
isolated; one storage error doesn't drop the whole email."
```

---

### Task 7: `email_inbound_handler` — promote to full router

**Files:**
- Modify: `apps/channels/channels_v2/email_channel.py:254-302` (handler)
- Modify: `apps/channels/tests/test_email_channel.py` (extend handler tests)

- [ ] **Step 1: Write failing tests**

Append to `apps/channels/tests/test_email_channel.py`:

```python
@pytest.mark.django_db()
class TestEmailInboundHandlerWithAttachments:
    def test_handler_persists_files_before_enqueue(self, team_with_users):
        team = team_with_users
        channel = _make_email_channel(team)
        pdf = _mime_part(filename="report.pdf", content_type="application/pdf", content=b"%PDF-...")
        inbound = _make_inbound_with_attachments([pdf], to_email=channel.extra_data["email_address"])

        with patch("apps.channels.channels_v2.email_channel.handle_email_message.delay") as delay:
            email_inbound_handler(sender=None, message=inbound, event=None)

        # one File saved
        assert File.objects.filter(team_id=team.id, name="report.pdf").count() == 1

        # delay called with attachment_file_ids in payload + channel_id
        delay.assert_called_once()
        kwargs = delay.call_args.kwargs
        assert kwargs["channel_id"] == channel.id
        assert len(kwargs["email_data"]["attachment_file_ids"]) == 1

    def test_handler_no_files_saved_when_no_channel_match(self, team_with_users):
        # No matching channel exists; handler must not save any Files
        pdf = _mime_part(filename="report.pdf", content_type="application/pdf", content=b"%PDF-")
        inbound = _make_inbound_with_attachments([pdf], to_email="nobody@example.com")

        with patch("apps.channels.channels_v2.email_channel.handle_email_message.delay") as delay:
            email_inbound_handler(sender=None, message=inbound, event=None)

        assert File.objects.count() == 0
        delay.assert_not_called()

    def test_skipped_attachments_appended_to_message_text(self, team_with_users):
        team = team_with_users
        channel = _make_email_channel(team)
        # Oversized file; should be rejected and surfaced
        big = b"x" * (21 * 1024 * 1024)
        oversized = _mime_part(filename="huge.pdf", content_type="application/pdf", content=big)
        inbound = _make_inbound_with_attachments(
            [oversized], to_email=channel.extra_data["email_address"], text="Please process"
        )

        with patch("apps.channels.channels_v2.email_channel.handle_email_message.delay") as delay:
            email_inbound_handler(sender=None, message=inbound, event=None)

        delay.assert_called_once()
        message_text = delay.call_args.kwargs["email_data"]["message_text"]
        assert "Please process" in message_text
        assert "huge.pdf" in message_text
        assert "skipped" in message_text.lower()
        # No file persisted (oversized)
        assert File.objects.filter(team_id=team.id).count() == 0

    def test_handler_passes_session_id_when_thread_continuation(self, team_with_users):
        team = team_with_users
        channel = _make_email_channel(team)
        session = _make_session(team, channel, external_id="<thread-anchor@example.com>")
        inbound = _make_inbound_message(
            to_email=channel.extra_data["email_address"],
            in_reply_to="<thread-anchor@example.com>",
            from_email="user@example.com",
        )
        inbound.attachments = []

        with patch("apps.channels.channels_v2.email_channel.handle_email_message.delay") as delay:
            email_inbound_handler(sender=None, message=inbound, event=None)

        delay.assert_called_once()
        assert delay.call_args.kwargs["session_id"] == session.id
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailInboundHandlerWithAttachments -v
```

Expected: tests fail — handler doesn't persist files / doesn't pass `channel_id`.

- [ ] **Step 3: Refactor `email_inbound_handler`**

Replace `email_inbound_handler` in `apps/channels/channels_v2/email_channel.py`:

```python
def email_inbound_handler(sender, message, event, **kwargs):
    """Handle inbound email from anymail's inbound signal.

    Performs full routing here (not in the Celery task) so attachments
    can be persisted to the team's File storage with team context known.
    The Celery payload carries File IDs, never raw bytes.

    Returns immediately so the ESP gets a fast 200 OK.
    """
    from apps.channels.datamodels import EmailMessage as EmailMessageDatamodel  # noqa: PLC0415
    from apps.channels.tasks import handle_email_message  # noqa: PLC0415

    if getattr(message, "spam_detected", None) is True:
        logger.info("Discarding spam email from %s", getattr(message, "from_email", "unknown"))
        return

    try:
        email_msg = EmailMessageDatamodel.parse(message)
    except Exception:
        logger.exception("Failed to parse inbound email")
        return

    channel, session = get_email_experiment_channel(
        in_reply_to=email_msg.in_reply_to,
        references=email_msg.references,
        to_address=email_msg.to_address,
        sender_address=email_msg.from_address,
    )
    if not channel:
        logger.info("No email channel found for to=%s, ignoring", email_msg.to_address)
        return

    set_current_team(channel.team)

    accepted_ids: list[int] = []
    skipped: list[dict] = []
    try:
        accepted_ids, skipped = _persist_inbound_attachments(
            email_msg._raw_attachments, team_id=channel.team_id
        )
    except Exception:
        logger.exception("Top-level failure persisting inbound attachments; proceeding with text only")

    email_msg.attachment_file_ids = accepted_ids
    email_msg.skipped_attachments = [SkippedAttachment(**s) for s in skipped]
    if skipped:
        email_msg.message_text = _augment_with_skip_notes(email_msg.message_text, skipped)

    handle_email_message.delay(
        email_data=email_msg.model_dump(),
        channel_id=channel.id,
        session_id=session.id if session else None,
    )


def _augment_with_skip_notes(message_text: str, skipped: list[dict]) -> str:
    """Append one bracketed line per skipped attachment to message_text so
    the LLM can surface the skip reasons to the user."""
    if not skipped:
        return message_text
    lines = [
        f"[Attachment {s['name']!r} ({_human_size(s['size'])}) skipped — {s['reason']}]"
        for s in skipped
    ]
    suffix = "\n\n" + "\n".join(lines)
    return (message_text or "").rstrip() + suffix


def _human_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "size unknown"
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}".rstrip("0").rstrip(".") + (f" {unit}" if unit != "B" and not f"{num_bytes:.1f}".rstrip("0").rstrip(".").endswith(unit) else "")
        num_bytes /= 1024  # ty: ignore[invalid-assignment]
    return f"{num_bytes:.1f} TB"
```

The `_human_size` formatter should be simplified — replace the convoluted one above with this cleaner version:

```python
def _human_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "size unknown"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
```

Also import `SkippedAttachment`:

```python
from apps.channels.datamodels import RawAttachment, SkippedAttachment
```

Imports for `set_current_team`:

```python
from apps.teams.utils import set_current_team
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest apps/channels/tests/test_email_channel.py -v --no-header -q 2>&1 | tail -30
```

Expected: all email channel tests pass (existing + new).

- [ ] **Step 5: Lint, format, type check**

```bash
uv run ruff check apps/channels/ --fix
uv run ruff format apps/channels/
uv run ty check apps/channels/
```

- [ ] **Step 6: Commit**

```bash
git add apps/channels/channels_v2/email_channel.py apps/channels/tests/test_email_channel.py
git commit -m "feat(email): full routing + attachment persistence in webhook handler

Promotes email_inbound_handler from best-effort pre-filter to the
complete router. Resolves channel and session, persists accepted
attachments synchronously, and enqueues handle_email_message with
channel_id, session_id, and File IDs only — no blob through the
Celery broker. Skipped attachment metadata is appended to message_text
so the LLM can mention rejections to the user."
```

---

### Task 8: `handle_email_message` task — accept channel_id/session_id; legacy fallback

**Files:**
- Modify: `apps/channels/tasks.py:217-258` (handle_email_message)
- Modify: `apps/channels/tests/test_email_channel.py` (extend `TestHandleEmailMessageTask`)

- [ ] **Step 1: Write failing tests**

Append to the existing `TestHandleEmailMessageTask` class in `apps/channels/tests/test_email_channel.py`:

```python
def test_task_uses_channel_id_when_provided(self, team_with_users):
    team = team_with_users
    experiment = ExperimentFactory(team=team)
    channel = ExperimentChannelFactory(
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data={"email_address": "bot@chat.openchatstudio.com"},
        team=team,
    )

    email_data = {
        "participant_id": "sender@example.com",
        "message_text": "Hello bot",
        "from_address": "sender@example.com",
        "to_address": "bot@chat.openchatstudio.com",
        "subject": "Test",
        "message_id": "<msg1@example.com>",
        "in_reply_to": None,
        "references": [],
        "attachment_file_ids": [],
        "skipped_attachments": [],
    }

    with patch("apps.channels.channels_v2.email_channel.EmailChannel") as MockEmailChannel:
        mock_instance = MockEmailChannel.return_value
        handle_email_message(email_data=email_data, channel_id=channel.id)
        MockEmailChannel.assert_called_once()
        # The channel passed to EmailChannel should be the one we looked up by id
        ec_kwarg = MockEmailChannel.call_args.kwargs["experiment_channel"]
        assert ec_kwarg.id == channel.id
        mock_instance.new_user_message.assert_called_once()


def test_task_legacy_payload_falls_back_to_routing(self, team_with_users):
    """Tasks queued before deploy won't carry channel_id; the task should
    still resolve the channel via the existing routing chain."""
    team = team_with_users
    experiment = ExperimentFactory(team=team)
    ExperimentChannelFactory(
        experiment=experiment,
        platform=ChannelPlatform.EMAIL,
        extra_data={"email_address": "bot@chat.openchatstudio.com"},
        team=team,
    )

    email_data = {
        "participant_id": "sender@example.com",
        "message_text": "Hello bot",
        "from_address": "sender@example.com",
        "to_address": "bot@chat.openchatstudio.com",
        "subject": "Test",
        "message_id": "<msg1@example.com>",
        "in_reply_to": None,
        "references": [],
        # No attachment_file_ids / skipped_attachments — pre-deploy payload shape
    }

    with patch("apps.channels.channels_v2.email_channel.EmailChannel") as MockEmailChannel:
        mock_instance = MockEmailChannel.return_value
        # Call without channel_id (legacy form)
        handle_email_message(email_data=email_data)
        MockEmailChannel.assert_called_once()
        mock_instance.new_user_message.assert_called_once()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestHandleEmailMessageTask::test_task_uses_channel_id_when_provided -v
```

Expected: TypeError — task signature doesn't accept `channel_id`.

- [ ] **Step 3: Update `handle_email_message`**

Modify `apps/channels/tasks.py` `handle_email_message` (replace the body):

```python
@shared_task(
    bind=True,
    base=TaskbadgerTask,
    ignore_result=True,
    autoretry_for=(OperationalError, ConnectionError),
    max_retries=3,
    retry_backoff=60,
    retry_backoff_max=300,
    retry_jitter=True,
)
def handle_email_message(self, email_data: dict, channel_id: int | None = None, session_id: int | None = None):
    from apps.channels.channels_v2.email_channel import (  # noqa: PLC0415
        EmailChannel,
        EmailThreadContext,
        get_email_experiment_channel,
    )
    from apps.channels.datamodels import EmailMessage as EmailMessageDatamodel  # noqa: PLC0415

    message = EmailMessageDatamodel(**email_data)

    if channel_id is not None:
        # Post-deploy payload: routing already happened in the webhook handler.
        try:
            experiment_channel = ExperimentChannel.objects.select_related("experiment", "team").get(id=channel_id)
        except ExperimentChannel.DoesNotExist:
            log.info("Email channel id=%s no longer exists, ignoring", channel_id)
            return
        session = None
        if session_id is not None:
            try:
                session = ExperimentSession.objects.select_related(
                    "team", "participant", "experiment_channel"
                ).get(id=session_id)
            except ExperimentSession.DoesNotExist:
                # Session was deleted between enqueue and dequeue; let the
                # pipeline create a fresh one rather than dropping the message.
                session = None
    else:
        # Legacy payload: in-flight tasks queued before this deploy don't
        # carry channel_id. Resolve by routing as the previous version did.
        experiment_channel, session = get_email_experiment_channel(
            in_reply_to=message.in_reply_to,
            references=message.references,
            to_address=message.to_address,
            sender_address=message.from_address,
        )
        if not experiment_channel:
            log.info("No email channel found for to=%s, ignoring", message.to_address)
            return

    set_current_team(experiment_channel.team)

    thread_context = EmailThreadContext.from_inbound(message)

    channel = EmailChannel(
        experiment=experiment_channel.experiment.default_version,
        experiment_channel=experiment_channel,
        experiment_session=session,
        thread_context=thread_context,
    )
    update_taskbadger_data(self, channel, message)
    channel.new_user_message(message)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestHandleEmailMessageTask -v
```

Expected: all task tests pass.

- [ ] **Step 5: Lint, format, type check**

```bash
uv run ruff check apps/channels/ --fix
uv run ruff format apps/channels/
uv run ty check apps/channels/
```

- [ ] **Step 6: Commit**

```bash
git add apps/channels/tasks.py apps/channels/tests/test_email_channel.py
git commit -m "refactor(email): handle_email_message uses channel_id from handler

Channel routing now happens in the webhook handler, so the Celery task
receives channel_id and session_id directly. The legacy code path
(no channel_id) is retained as a fallback so in-flight pre-deploy
tasks still process correctly; can be removed after one release cycle."
```

---

### Task 9: `EmailSender` stateful — buffer text/files, send on flush

**Files:**
- Modify: `apps/channels/channels_v2/email_channel.py:158-189` (EmailSender)
- Modify: `apps/channels/tests/test_email_channel.py` (extend `TestEmailSender`)

- [ ] **Step 1: Write failing tests**

Append to the existing `TestEmailSender` class in `apps/channels/tests/test_email_channel.py`:

```python
def test_send_text_alone_sends_one_email(self, mailoutbox):
    sender = EmailSender(
        from_address="bot@chat.openchatstudio.com",
        domain="chat.openchatstudio.com",
        thread_context=EmailThreadContext(subject="Re: Hi"),
    )

    sender.send_text("Hello", "user@example.com")
    assert len(mailoutbox) == 0  # not sent yet — flush is required

    sender.flush()
    assert len(mailoutbox) == 1
    msg = mailoutbox[0]
    assert msg.body == "Hello"
    assert msg.to == ["user@example.com"]
    assert msg.attachments == []


def test_send_text_then_files_sends_one_combined_email(self, mailoutbox, team_with_users):
    team = team_with_users
    file1 = File.create(
        filename="a.pdf", file_obj=BytesIO(b"%PDF-A"), team_id=team.id,
        purpose="message_media", content_type="application/pdf",
    )
    file2 = File.create(
        filename="b.csv", file_obj=BytesIO(b"a,b\n1,2"), team_id=team.id,
        purpose="message_media", content_type="text/csv",
    )

    sender = EmailSender(
        from_address="bot@chat.openchatstudio.com",
        domain="chat.openchatstudio.com",
        thread_context=EmailThreadContext(
            subject="Re: docs",
            in_reply_to="<orig@example.com>",
            references=["<orig@example.com>"],
        ),
    )
    sender.send_text("Here are the docs.", "user@example.com")
    sender.send_file(file1, "user@example.com", session_id=1)
    sender.send_file(file2, "user@example.com", session_id=1)
    sender.flush()

    assert len(mailoutbox) == 1
    msg = mailoutbox[0]
    assert msg.body == "Here are the docs."
    assert msg.subject == "Re: docs"
    assert msg.extra_headers.get("In-Reply-To") == "<orig@example.com>"
    assert "<orig@example.com>" in msg.extra_headers.get("References", "")
    # Two attachments
    names = {a[0] for a in msg.attachments}
    assert names == {"a.pdf", "b.csv"}


def test_flush_with_nothing_queued_is_noop(self, mailoutbox):
    sender = EmailSender(
        from_address="bot@chat.openchatstudio.com",
        domain="chat.openchatstudio.com",
    )
    sender.flush()
    assert len(mailoutbox) == 0


def test_flush_resets_state(self, mailoutbox):
    sender = EmailSender(
        from_address="bot@chat.openchatstudio.com",
        domain="chat.openchatstudio.com",
        thread_context=EmailThreadContext(subject="Re: ad-hoc"),
    )
    sender.send_text("First", "user@example.com")
    sender.flush()
    sender.send_text("Second", "user@example.com")
    sender.flush()
    assert len(mailoutbox) == 2
    assert mailoutbox[0].body == "First"
    assert mailoutbox[1].body == "Second"
```

The `mailoutbox` fixture is provided by `pytest-django`. Confirm it's available; the project uses `pytest-django` already (see existing tests in this file using `mail.outbox`).

If `mailoutbox` isn't available as a fixture, replace usages with `from django.core import mail; mail.outbox`. Check first:

```bash
grep -n "mailoutbox\|mail.outbox" apps/channels/tests/test_email_channel.py | head -5
```

If existing tests use `mail.outbox`, adapt by adding `mail.outbox = []` in `setup_method` or use `mailoutbox` fixture.

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailSender -v
```

Expected: failures — `flush` doesn't exist on EmailSender, send_text sends immediately.

- [ ] **Step 3: Refactor `EmailSender`**

Replace `EmailSender` in `apps/channels/channels_v2/email_channel.py`:

```python
class EmailSender(ChannelSender):
    """Sends a single threaded email reply combining text body and any
    file attachments. State accumulates across send_text/send_file calls
    and is committed on flush()."""

    def __init__(
        self,
        from_address: str,
        domain: str,
        thread_context: EmailThreadContext | None = None,
    ):
        self.from_address = from_address
        self.domain = domain
        self.thread_context = thread_context or EmailThreadContext()
        self.last_message_id: str | None = None
        self._body: str | None = None
        self._recipient: str | None = None
        self._attachments: list[tuple[str, bytes, str]] = []

    def send_text(self, text: str, recipient: str) -> None:
        self._body = text
        self._recipient = recipient

    def send_file(self, file, recipient: str, session_id: int) -> None:
        if self._recipient is None:
            self._recipient = recipient
        with file.file.open("rb") as fh:
            content = fh.read()
        self._attachments.append((
            file.name,
            content,
            file.content_type or "application/octet-stream",
        ))

    def flush(self) -> None:
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
        # Reset for ad-hoc / multi-message sends
        self._body = None
        self._recipient = None
        self._attachments = []
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailSender -v
```

Expected: all sender tests pass (existing + new).

- [ ] **Step 5: Lint, format, type check**

```bash
uv run ruff check apps/channels/ --fix
uv run ruff format apps/channels/
uv run ty check apps/channels/
```

- [ ] **Step 6: Commit**

```bash
git add apps/channels/channels_v2/email_channel.py apps/channels/tests/test_email_channel.py
git commit -m "feat(email): EmailSender buffers and sends on flush

Reworks EmailSender to stage the body and attachments across
send_text/send_file calls and commit them as a single Django
EmailMessage in flush(). This is what makes outbound text +
attachments arrive in the user's inbox as one threaded message
instead of multiple replies."
```

---

### Task 10: `EmailChannel` capabilities — supports_files=True + can_send_file

**Files:**
- Modify: `apps/channels/channels_v2/email_channel.py:223-230` (EmailChannel._get_capabilities)
- Modify: `apps/channels/tests/test_email_channel.py` (extend `TestEmailChannel`)

- [ ] **Step 1: Write failing tests**

Replace / extend the existing `test_capabilities` in `TestEmailChannel`:

```python
def test_capabilities_supports_files(self):
    channel_mock = MagicMock()
    channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
    experiment_mock = MagicMock()

    email_channel = EmailChannel(experiment_mock, channel_mock)
    caps = email_channel._get_capabilities()

    assert caps.supports_voice_replies is False
    assert caps.supports_files is True
    assert caps.supports_conversational_consent is False
    assert caps.supports_static_triggers is True
    assert MESSAGE_TYPES.TEXT in caps.supported_message_types

def test_can_send_file_normal_pdf(self):
    channel_mock = MagicMock()
    channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
    email_channel = EmailChannel(MagicMock(), channel_mock)

    file = MagicMock()
    file.content_type = "application/pdf"
    file.content_size = 1024 * 1024  # 1 MB

    assert email_channel._can_send_file(file) is True

def test_can_send_file_rejects_oversized(self):
    channel_mock = MagicMock()
    channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
    email_channel = EmailChannel(MagicMock(), channel_mock)

    file = MagicMock()
    file.content_type = "application/pdf"
    file.content_size = 25 * 1024 * 1024  # 25 MB > 20 MB

    assert email_channel._can_send_file(file) is False

def test_can_send_file_rejects_denylisted(self):
    channel_mock = MagicMock()
    channel_mock.extra_data = {"email_address": "bot@chat.openchatstudio.com"}
    email_channel = EmailChannel(MagicMock(), channel_mock)

    file = MagicMock()
    file.content_type = "application/x-msdownload"
    file.content_size = 1024

    assert email_channel._can_send_file(file) is False
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailChannel -v
```

Expected: `supports_files is True` assertion fails (current value is False); `_can_send_file` doesn't exist.

- [ ] **Step 3: Wire capabilities + can_send_file**

Modify `apps/channels/channels_v2/email_channel.py` `EmailChannel`:

```python
class EmailChannel(ChannelBase):
    """Email channel — text + file attachments, no voice, no conversational consent."""

    voice_replies_supported = False
    supported_message_types = (MESSAGE_TYPES.TEXT,)

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
        *,
        thread_context: EmailThreadContext | None = None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        self.thread_context = thread_context or EmailThreadContext()
        self._sender_instance: EmailSender | None = None

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_sender(self) -> EmailSender:
        extra = self.experiment_channel.extra_data
        email_address = extra.get("email_address", "")
        self._sender_instance = EmailSender(
            from_address=extra.get("from_address") or email_address,
            domain=_domain_from_address(email_address),
            thread_context=self.thread_context,
        )
        return self._sender_instance

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=self.voice_replies_supported,
            supports_files=True,
            supports_conversational_consent=False,
            supports_static_triggers=True,
            supported_message_types=self.supported_message_types,
            can_send_file=self._can_send_file,
        )

    def _can_send_file(self, file) -> bool:
        return can_send_on_email(file.content_type, file.content_size).supported

    def new_user_message(self, message):
        # ... unchanged from existing implementation
        # (keep the existing body for capturing Message-ID, etc.)
        ...
```

Keep the existing `new_user_message` body intact — only `_get_capabilities` and the new `_can_send_file` are changed/added.

Add the import for `can_send_on_email` near the top of `email_channel.py`:

```python
from apps.service_providers.file_limits import (
    EMAIL_BLOCKED_CONTENT_TYPES,
    EMAIL_BLOCKED_EXTENSIONS,
    EMAIL_MAX_ATTACHMENT_BYTES,
    EMAIL_TEXT_LIKE_APPLICATION_TYPES,
    can_send_on_email,
)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailChannel -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full email channel suite**

```bash
uv run pytest apps/channels/tests/test_email_channel.py -v --no-header -q 2>&1 | tail -40
```

Expected: no regressions.

- [ ] **Step 6: Lint, format, type check**

```bash
uv run ruff check apps/channels/ --fix
uv run ruff format apps/channels/
uv run ty check apps/channels/
```

- [ ] **Step 7: Commit**

```bash
git add apps/channels/channels_v2/email_channel.py apps/channels/tests/test_email_channel.py
git commit -m "feat(email): enable file capabilities on EmailChannel

Sets supports_files=True and wires _can_send_file via the
file_limits.can_send_on_email checker. Files that fail the size
or denylist check fall back to inline download URLs in the body
via the existing ResponseFormattingStage logic."
```

---

### Task 11: End-to-end integration test

**Files:**
- Test: `apps/channels/tests/test_email_channel.py` (new `TestEmailAttachmentsEndToEnd` class)

- [ ] **Step 1: Write integration test**

Append to `apps/channels/tests/test_email_channel.py`:

```python
@pytest.mark.django_db()
class TestEmailAttachmentsEndToEnd:
    def test_inbound_attachment_persisted_and_visible_to_pipeline(
        self, team_with_users, mailoutbox
    ):
        team = team_with_users
        channel = _make_email_channel(team)

        pdf = _mime_part(filename="report.pdf", content_type="application/pdf", content=b"%PDF-test")
        inbound = _make_inbound_with_attachments(
            [pdf], to_email=channel.extra_data["email_address"], text="See attached"
        )

        # Use the real Celery task synchronously: handler -> handle_email_message
        # Patch only the bot interaction so we don't need an LLM provider.
        captured: dict = {}

        def fake_process_input(self, user_query, attachments=None, human_message=None):
            captured["attachments"] = attachments
            captured["query"] = user_query
            from apps.chat.models import ChatMessage, ChatMessageType  # noqa: PLC0415
            return ChatMessage(content="ack: got the file", message_type=ChatMessageType.AI)

        with (
            patch("apps.channels.channels_v2.stages.core.get_bot") as mock_get_bot,
            patch("apps.channels.channels_v2.email_channel.handle_email_message.delay",
                  side_effect=lambda **kw: handle_email_message(**kw)),
        ):
            mock_bot = MagicMock()
            mock_bot.process_input.side_effect = lambda q, attachments=None, human_message=None: fake_process_input(
                None, q, attachments=attachments, human_message=human_message
            )
            mock_get_bot.return_value = mock_bot

            email_inbound_handler(sender=None, message=inbound, event=None)

        # File was persisted to the right team
        files = File.objects.filter(team_id=team.id, name="report.pdf")
        assert files.count() == 1
        # Bot saw the attachment
        assert captured["attachments"] is not None
        assert len(captured["attachments"]) == 1
        assert captured["attachments"][0].file_id == files.first().id
        # Outbound email sent
        assert len(mailoutbox) == 1
        assert mailoutbox[0].body.startswith("ack: got the file")

    def test_outbound_attachment_combined_with_text(self, team_with_users, mailoutbox):
        team = team_with_users
        channel = _make_email_channel(team)
        session = _make_session(team, channel, external_id="<thread@chat.openchatstudio.com>")

        outbound_file = File.create(
            filename="result.csv",
            file_obj=BytesIO(b"x,y\n1,2\n"),
            team_id=team.id,
            purpose="message_media",
            content_type="text/csv",
        )

        # Simulate an ad hoc outbound message (e.g. from a Python node) carrying a file.
        with patch.object(EmailChannel, "_get_callbacks", return_value=ChannelCallbacks()):
            email_channel = EmailChannel(
                experiment=channel.experiment.default_version,
                experiment_channel=channel,
                experiment_session=session,
                thread_context=EmailThreadContext(subject="Re: thread"),
            )
            email_channel.send_message_to_user("Here is your CSV.", files=[outbound_file])

        assert len(mailoutbox) == 1
        msg = mailoutbox[0]
        assert msg.body == "Here is your CSV."
        names = {a[0] for a in msg.attachments}
        assert "result.csv" in names
```

- [ ] **Step 2: Run integration tests**

```bash
uv run pytest apps/channels/tests/test_email_channel.py::TestEmailAttachmentsEndToEnd -v
```

Expected: both tests pass. If they fail because of `get_bot` import path, adjust the patch target — find with:

```bash
grep -n "from apps.chat.bots\|get_bot" apps/channels/channels_v2/stages/core.py | head
```

- [ ] **Step 3: Run full email + attachment hydration test suites**

```bash
uv run pytest apps/channels/tests/test_email_channel.py apps/channels/tests/channels/stages/test_attachment_hydration.py apps/channels/tests/channels/stages/test_response_sending.py apps/service_providers/tests/test_file_limits.py -v --no-header -q 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 4: Run a broader smoke check**

```bash
uv run pytest apps/channels/ -q 2>&1 | tail -20
```

Expected: no regressions across the channels app.

- [ ] **Step 5: Lint, format, type check**

```bash
uv run ruff check apps/ --fix
uv run ruff format apps/
uv run ty check apps/channels/ apps/service_providers/
```

- [ ] **Step 6: Commit**

```bash
git add apps/channels/tests/test_email_channel.py
git commit -m "test(email): end-to-end inbound + outbound attachment flow

Two integration tests covering the full path: (1) inbound — webhook
handler persists a PDF, the pipeline hydrates it, and the bot sees
it on message.attachments; (2) outbound — text + CSV via send_message_to_user
arrive in the user's inbox as a single email."
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `EMAIL_*` constants and `can_send_on_email` | Task 1 |
| `ChannelSender.flush()` no-op | Task 2 |
| `ResponseSendingStage` calls flush | Task 2 |
| `BaseMessage.attachment_file_ids` | Task 3 |
| `AttachmentHydrationStage` in core | Task 4 |
| Default pipeline includes hydration stage | Task 4 |
| `EmailMessage.parse()` extracts attachments | Task 5 |
| `RawAttachment`, `SkippedAttachment`, `_raw_attachments` | Task 5 |
| `_persist_inbound_attachments`, `_is_blocked`, `_detect_content_type`, `_category` | Task 6 |
| `email_inbound_handler` full router | Task 7 |
| Skip notes appended to `message_text` | Task 7 |
| `handle_email_message` accepts `channel_id`/`session_id` | Task 8 |
| Legacy fallback for in-flight tasks | Task 8 |
| `EmailSender` stateful, `flush()` overridden | Task 9 |
| `EmailChannel.supports_files=True`, `can_send_file` | Task 10 |
| Cross-category mismatch detection (text-like allowlist) | Task 6 (`_is_blocked` matrix) |
| End-to-end inbound + outbound | Task 11 |

All spec items mapped to a task.

**Placeholder scan:** No "TBD", "TODO", or hand-wavy steps. Every code change is shown verbatim.

**Type consistency:**
- `_persist_inbound_attachments` returns `tuple[list[int], list[dict]]` — used the same way in Task 7 (`accepted_ids, skipped = ...` then `[SkippedAttachment(**s) for s in skipped]`).
- `EmailSender._attachments: list[tuple[str, bytes, str]]` — matches `msg.attach(filename, content, mimetype)` Django API.
- `flush()` signature `() -> None` consistent across base, StubSender, EmailSender.
- `channel_id: int | None = None` consistent in `handle_email_message` signature, both delay call site (Task 7) and task definition (Task 8).
- `attachment_file_ids: list[int]` consistent across `BaseMessage` definition (Task 3), datamodel test (Task 3), `AttachmentHydrationStage.process` (Task 4), handler payload (Task 7), and task body (Task 8).

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-29-email-attachments.md`.**
