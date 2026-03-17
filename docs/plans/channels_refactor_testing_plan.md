# Channel Refactor Testing Plan

## Context

We are refactoring `apps/chat/channels.py` into a new stage-based pipeline architecture (reference: `docs/plans/channels_refactor_example.py`). The new implementation will be built alongside the old one. New tests are written from scratch — existing tests are not touched.

Once the new tests pass against the new implementation, we do a hard switchover: delete the old `channels.py` and old tests, update `apps/channels/tasks.py` import paths, done.

**Goal here:** Define the full test suite for the new architecture before writing a line of implementation.

---

## Existing Test Landscape (for reference, not modification)

| File | What it tests |
|---|---|
| `apps/channels/tests/test_base_channel_behavior.py` | 995 lines — the behavioral contract we must replicate |
| `apps/channels/tests/test_slack_channel.py` | SlackChannel message routing + file handling |
| `apps/channels/tests/test_web_channel.py` | WebChannel session creation + versioning |
| `apps/channels/tests/test_evaluation_channel.py` | EvaluationChannel init + tracing |
| `apps/channels/tests/test_whatsapp_integration.py` | WhatsApp message parsing + handling |
| `apps/channels/tests/test_connect_integration.py` | CommCareConnect encrypted messaging |
| `apps/chat/tests/test_channel_format_reference_section.py` | Isolated unit test for reference section formatting |

**Integration boundaries** (where channels are instantiated in production):
- `apps/channels/tasks.py` — all webhook handlers call `channel.new_user_message(message)`

---

## New Architecture Components (from refactor example)

**Data / exceptions:**
- `ChannelCapabilities` — frozen dataclass (voice, files, consent, message types, `can_send_file` callable)
- `MessageProcessingContext` — mutable dataclass carrying all pipeline state
- `EarlyExitResponse(Exception)` — raised by core stages to short-circuit

**Protocols:**
- `ChannelCallbacks` — no-op base hooks (transcription_started, echo_transcript, submit_input_to_llm, get_message_audio)
- `ChannelSender(ABC)` — send_text, send_voice, send_file

**Pipeline:**
- `ProcessingStage(ABC)` — `should_run(ctx)` + `process(ctx)`; callable via `__call__`
- `MessageProcessingPipeline` — runs core stages, catches `EarlyExitResponse`, always runs terminal stages

**Core stages** (can raise `EarlyExitResponse`):
`ParticipantValidationStage` → `SessionResolutionStage` → `MessageTypeValidationStage` → `ConsentFlowStage` → `QueryExtractionStage` → `ChatMessageCreationStage` → `BotInteractionStage` → `ResponseFormattingStage`

**Terminal stages** (always run):
`EarlyExitResponseStage` → `ResponseSendingStage` → `ActivityTrackingStage`

---

## Test File Structure

All new tests live under `apps/channels/tests/new_arch/` — sibling to existing test files so pytest finds them without config changes.

```
apps/channels/tests/new_arch/
    __init__.py
    conftest.py                          # TestNewChannel, TestNewSender, TestNewCallbacks, make_context()
    test_pipeline_integration.py         # End-to-end bounding box (replaces test_base_channel_behavior.py)
    test_pipeline_orchestrator.py        # MessageProcessingPipeline unit tests (mock stages)
    stages/
        __init__.py
        test_participant_validation.py
        test_session_resolution.py
        test_message_type_validation.py
        test_consent_flow.py
        test_query_extraction.py
        test_chat_message_creation.py
        test_bot_interaction.py
        test_response_formatting.py
        test_early_exit_response.py      # EarlyExitResponseStage (terminal)
        test_response_sending.py         # ResponseSendingStage (terminal)
        test_activity_tracking.py        # ActivityTrackingStage (terminal)
    senders/
        __init__.py
        test_telegram_sender.py
        test_whatsapp_sender.py
    callbacks/
        __init__.py
        test_telegram_callbacks.py
        test_whatsapp_callbacks.py
    concrete/
        __init__.py
        test_telegram_channel.py
        test_whatsapp_channel.py
```

---

## `conftest.py` Design

This is the linchpin. It provides the stubs used across all test layers.

### `TestNewSender` — captures outbound messages for assertions
```python
class TestNewSender(ChannelSender):
    def __init__(self):
        self.text_messages = []   # list of (text, recipient)
        self.voice_messages = []  # list of (audio, recipient)
        self.files_sent = []      # list of (file, recipient)

    def send_text(self, text, recipient): self.text_messages.append((text, recipient))
    def send_voice(self, audio, recipient): self.voice_messages.append((audio, recipient))
    def send_file(self, file, recipient): self.files_sent.append((file, recipient))
```

### `TestNewCallbacks` — records callback invocations
```python
class TestNewCallbacks(ChannelCallbacks):
    def __init__(self):
        self.transcription_started_calls = []
        self.echo_transcript_calls = []
        self.submit_input_calls = []
    def transcription_started(self, recipient): self.transcription_started_calls.append(recipient)
    def echo_transcript(self, recipient, transcript): self.echo_transcript_calls.append((recipient, transcript))
    def submit_input_to_llm(self, recipient): self.submit_input_calls.append(recipient)
    def get_message_audio(self, message): return BytesIO(b"fake_audio")
```

### `TestNewChannel` — minimal concrete channel for integration tests
```python
class TestNewChannel(ChannelBase):
    voice_replies_supported = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]

    def __init__(self, *args, capabilities=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._sender = TestNewSender()
        self._callbacks = TestNewCallbacks()
        self._override_capabilities = capabilities

    def _get_sender(self): return self._sender
    def _get_callbacks(self): return self._callbacks
    def _get_capabilities(self):
        return self._override_capabilities or ChannelCapabilities(
            supports_voice=True, supports_files=False,
            supports_conversational_consent=True,
            supported_message_types=[MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE],
        )

    # Convenience for assertions
    @property
    def text_sent(self): return [t for t, _ in self._sender.text_messages]
    @property
    def voice_sent(self): return [a for a, _ in self._sender.voice_messages]
```

### `make_context()` — builds minimal `MessageProcessingContext` for stage unit tests
```python
def make_context(*, message=None, experiment=None, experiment_channel=None,
                 experiment_session=None, sender=None, callbacks=None,
                 capabilities=None, participant_identifier="test_user_123",
                 participant_allowed=True, user_query=None, bot_response=None,
                 early_exit_response=None, **extra):
    return MessageProcessingContext(
        message=message or text_message(),  # from base_messages
        experiment=experiment or ExperimentFactory.build(),
        experiment_channel=experiment_channel or ExperimentChannelFactory.build(),
        callbacks=callbacks or TestNewCallbacks(),
        sender=sender or TestNewSender(),
        capabilities=capabilities or make_capabilities(),
        trace_service=make_trace_service(),  # MagicMock with context manager support
        experiment_session=experiment_session,
        participant_identifier=participant_identifier,
        participant_allowed=participant_allowed,
        user_query=user_query,
        bot_response=bot_response,
        early_exit_response=early_exit_response,
        **extra,
    )
```

**Key design:** `make_context()` uses `.build()` (no DB) by default. Tests needing DB use real factories and `@pytest.mark.django_db()`.

---

## Layer 1: Integration Tests (`test_pipeline_integration.py`)

**Purpose:** Bounding box. Validates `TestNewChannel.new_user_message()` behavioral parity with the old architecture.

**Standard mock:** `@patch("apps.chat.bots.PipelineBot.process_input", return_value=ChatMessage(content="OK"))`

**Scenarios to cover** (mapped from existing `test_base_channel_behavior.py`):

| Scenario | Old test |
|---|---|
| First message creates session linked to channel | `test_incoming_message_adds_channel_info` |
| Second message from same participant reuses session | `test_incoming_message_uses_existing_experiment_session` |
| Ended sessions not reused; new session created | `test_non_active_sessions_are_not_reused` |
| Different participants get separate sessions | `test_different_sessions_created_for_different_users` |
| Same participant across teams → separate Participant records | `test_different_participants_created_for_same_user_in_different_teams` |
| Same participant across experiments → reused Participant | `test_participant_reused_across_experiments` |
| `/reset` with prior engagement → new session | `test_reset_command_creates_new_experiment_session` |
| `/reset` with no engagement → no new session | `test_reset_conversation_does_not_create_new_session` |
| Full consent flow: SETUP → PENDING → PENDING_PRE_SURVEY → ACTIVE | `test_pre_conversation_flow` |
| Unsupported message type → AI-generated refusal | `test_unsupported_message_type_*` |
| Voice ALWAYS → voice reply | `test_voice_response_behaviour` |
| Voice NEVER → text reply | parameterized |
| Voice RECIPROCAL + voice input → voice; + text input → text | parameterized |
| Voice reply with URLs → voice + URL text fallback | `test_voice_response_with_urls` |
| Voice message → `voice` tag on ChatMessage | `test_voice_tag_created_on_message` |
| No synthetic voice → text reply | `test_reply_with_text_when_synthetic_voice_not_specified` |
| Non-allowlisted participant → refusal, no session | `test_participant_authorization` |
| Versioned experiment → version tracked on session after each message | `test_incoming_message_adds_version_on_session` |
| New sessions link to working experiment (not a version) | `test_new_sessions_are_linked_to_the_working_experiment` |
| Supported attachments sent directly; unsupported appended as links | `test_supported_and_unsupported_attachments` |
| `GenerationCancelled` → empty ChatMessage, no exception | `test_chat_message_returned_for_cancelled_generate` |
| Failed transcription → user informed | `test_failed_transcription_informs_the_user` |
| Bot error → user informed, `processing_errors` updated | (new) |
| Audio synthesis failure → notification + text fallback | `TestNotifications.*` |
| File delivery failure → notification + download link sent | `TestNotifications.*` |
| Transcription failure → notification triggered | `TestNotifications.*` |
| Pre-existing session on context → SessionResolution is no-op (Web/Slack) | new |
| EarlyExitResponse from any core stage → terminal stages still run | new |
| `ResponseSendingStage` is the ONLY place *responses* reach the user (callbacks may send indicators/echoes mid-pipeline) | new |

---

## Layer 2: Pipeline Orchestrator Tests (`test_pipeline_orchestrator.py`)

No DB needed. All stages are `MagicMock` instances.

- All core stages run in order on happy path; terminal stages follow.
- Core stage N raises `EarlyExitResponse` → stages N+1..M skipped; `ctx.early_exit_response` set.
- ALL terminal stages run regardless of early exit (even if stage 1 raised).
- `stage.should_run(ctx)` returning `False` → `process(ctx)` not called.
- `None` entries in stage lists are filtered out by constructor.
- Returns the final `MessageProcessingContext`.

---

## Layer 3: Stage Unit Tests

**Pattern:** `make_context()` with only the fields the stage needs → `stage(ctx)` → assert on `ctx` mutations or raised `EarlyExitResponse`.

### `ParticipantValidationStage`
- Public experiment → `participant_allowed=True`, no exception.
- Private + in allowlist → `participant_allowed=True`.
- Private + not in allowlist → raises `EarlyExitResponse`.
- `ctx.participant_identifier` set from `message.participant_id` in all cases.
- Mock: `experiment.is_public`, `experiment.is_participant_allowed()` as MagicMock attrs.

### `SessionResolutionStage` (`@pytest.mark.django_db`)
- `participant_allowed=False` → `should_run()` returns False.
- Pre-set session on ctx → no-op (no DB queries for session).
- No existing session → new session created.
- Active session exists → reused.
- Completed session → excluded by query → new session created.
- `/reset` text (case-insensitive, stripped) + no session → new session + `EarlyExitResponse("Conversation reset")`.
- `/reset` + existing session where `user_already_engaged()=True` → session ended + new session + `EarlyExitResponse`.
- `/reset` + existing session where `user_already_engaged()=False` → no reset.
- New session always links to `experiment.get_working_version()`.

### `MessageTypeValidationStage`
- Supported type → no exception, ctx unchanged.
- Unsupported type → raises `EarlyExitResponse` with bot-generated or fallback text.
- `EventBot.get_user_message` fails → fallback string, `ctx.processing_errors` updated.

### `ConsentFlowStage` (`@pytest.mark.django_db`)
- `supports_conversational_consent=False` → `should_run()` False.
- Consent not enabled → `should_run()` False, side effect: session set to ACTIVE.
- Session already ACTIVE → `should_run()` False.
- SETUP → PENDING + raises `EarlyExitResponse` with consent text.
- PENDING + non-consent input → repeat consent prompt.
- PENDING + `"1"` + no pre-survey → ACTIVE, no early exit (or seed message early exit).
- PENDING + `"1"` + pre-survey → PENDING_PRE_SURVEY + survey prompt.
- PENDING_PRE_SURVEY + non-consent → repeat survey.
- PENDING_PRE_SURVEY + `"1"` → ACTIVE, seed message processed if present.
- `ctx.bot` lazily created once and reused.

### `QueryExtractionStage`
- Text message → `ctx.user_query = message.message_text`, no callbacks called.
- Voice message → `transcription_started()` called → `get_message_audio()` → transcription → `ctx.user_query` set.
- `echo_transcript=True` → `echo_transcript()` called; `False` → not called.
- Transcription failure → `audio_transcription_failure_notification` called, exception re-raised.
- No voice provider → `UserReportableError` raised.

### `ChatMessageCreationStage` (`@pytest.mark.django_db`)
- `user_query is None` → `should_run()` False.
- Text message → `ChatMessage(HUMAN)` created, no voice tag.
- Voice + `cached_media_data` → `File` created, attached to chat, in `metadata["ocs_attachment_file_ids"]`.
- Voice message → HUMAN message gets `voice` tag.
- `ctx.human_message` set; `trace_service.set_input_message_id()` called.
- Attachments → IDs in metadata.

### `BotInteractionStage`
- `user_query is None` → `should_run()` False.
- `submit_input_to_llm()` called before bot.
- `ctx.bot` is None → bot created lazily; already set → reused.
- Successful call → `ctx.bot_response` and `ctx.files_to_send` set.
- Bot raises → `EventBot` called for error message → `EarlyExitResponse` raised with that message.
- `EventBot` also fails → default error string used.

### `ResponseFormattingStage`
- `bot_response is None` → `should_run()` False.
- Voice ALWAYS + voice provider → `ctx.voice_audio` set, URLs in `ctx.additional_text_message`.
- Voice NEVER → text path, `ctx.voice_audio` is None.
- Voice RECIPROCAL: voice input → voice; text input → text.
- `supports_files=False` → all files in `unsupported_files`.
- `supports_files=True` → files split via `can_send_file`.
- Unsupported files appended as links to `ctx.formatted_message` (text path).
- Voice path + unsupported files → in `ctx.additional_text_message`.

### `EarlyExitResponseStage` (terminal) (`@pytest.mark.django_db`)
- `early_exit_response is None` → `should_run()` False.
- `experiment_session is None` → no ChatMessage created.
- Session present → `ChatMessage(AI, content=early_exit_response)` created.

### `ResponseSendingStage` (terminal)
- Both `formatted_message` and `early_exit_response` are None → `should_run()` False.
- Early exit path → `sender.send_text(early_exit_response)`, no voice/file sends.
- Normal text path → `sender.send_text(formatted_message)`.
- Normal voice path → `sender.send_voice()`; `additional_text_message` present → `sender.send_text()` too.
- Voice send fails → notification + fallback text.
- Files in `files_to_send` → `sender.send_file()` per file.
- File send fails → notification + download link sent via `sender.send_text()`.

### `ActivityTrackingStage` (terminal)
- `experiment_session is None` → `should_run()` False.
- `session.last_activity_at` updated.
- `is_a_version=True` + version not in list → appended; already in list → no duplicate.
- `is_a_version=False` → `experiment_versions` not touched.
- `session.save(update_fields=[...])` called with correct fields.

---

## Layer 4: Sender & Callbacks Tests

### `TelegramSender` (mock `telegram_bot` at constructor)
- Short text → `antiflood(send_message)` called once.
- Long text → multiple chunks → called multiple times.
- `ApiTelegramException(403, "bot was blocked")` → raises `ChannelException`.
- Other `ApiTelegramException` → raises `ChannelException`.
- Voice send → `antiflood(send_voice, audio, duration)`.
- File: image→`send_photo`, video→`send_video`, audio→`send_audio`, other→`send_document`.

### `WhatsappSender` (mock `messaging_service`)
- `send_text()` → `service.send_text_message(platform=WHATSAPP)`.
- `send_voice()` → `service.send_voice_message()`.
- `send_file()` → `service.send_file_to_user(platform=WHATSAPP)`.

### `TelegramCallbacks` (mock `TelegramSender` + `telegram_bot`)
- `transcription_started()` → `send_chat_action(action="upload_voice")` via raw bot.
- `submit_input_to_llm()` → `send_chat_action(action="typing")` via raw bot.
- `echo_transcript()` → `sender.send_text()` with `"I heard: {transcript}"`.
- `get_message_audio()` → mocked `httpx.get` + `audio.convert_audio()` (OGG→WAV).

### `WhatsappCallbacks`
- `echo_transcript()` → delegates to `sender.send_text("I heard: ...")`.

---

## Layer 5: Concrete Channel Tests

### `TelegramChannel` (mostly no DB — use `.build()`)
- `__init__()` instantiates `TeleBot` with `extra_data["bot_token"]`.
- `_get_callbacks()` returns `TelegramCallbacks`.
- `_get_sender()` returns `TelegramSender`.
- `_get_capabilities()` → `supports_voice=True, supports_files=True, [TEXT, VOICE]`.
- `_can_send_file()`: image <10MB→True; image >10MB→False; video <50MB→True; unknown MIME→False.
- `_build_pipeline()` → pipeline with correct stage types and count.

### `WhatsappChannel`
- `messaging_service` lazily resolved and cached.
- `_get_capabilities()` delegates all fields from messaging service.
- `_get_sender()` uses `extra_data["number"]` as `from_number`.
- `_get_callbacks()` wraps the sender.

---

## Reusable Existing Infrastructure

| What | Where |
|---|---|
| `ExperimentFactory`, `ExperimentSessionFactory`, `ExperimentChannelFactory` | `apps/utils/factories/experiment.py` |
| `TeamWithUsersFactory`, `MembershipFactory` | `apps/utils/factories/` |
| `MessagingProviderFactory` | `apps/utils/factories/service_provider_factories.py` |
| `twilio_provider`, `turn_io_provider`, `slack_provider` fixtures | `apps/channels/tests/conftest.py` (inherited by pytest) |
| `base_messages.text_message()`, `base_messages.audio_message()` | `apps/channels/tests/message_examples/base_messages.py` |
| `telegram_messages.*` | `apps/channels/tests/message_examples/telegram_messages.py` |
| Global `experiment` fixture | `apps/conftest.py` |

---

## Mock Strategy

| Target | How |
|---|---|
| Bot processing | `@patch("apps.chat.bots.PipelineBot.process_input")` |
| EventBot error messages | `@patch("apps.chat.bots.EventBot.get_user_message")` |
| Voice transcription | `@patch` on speech service or override `TestNewCallbacks.get_message_audio` |
| Voice synthesis | `@patch("apps.service_providers.models.VoiceProvider.get_speech_service")` |
| Telegram bot API | `MagicMock` passed to `TelegramSender`/`Callbacks` constructors |
| WhatsApp messaging service | `MagicMock` passed to `WhatsappSender` constructor |
| Trace service | `make_trace_service()` — MagicMock with context manager |
| Notifications | `@patch("apps.ocs_notifications.notifications.<func>")` |
| Static triggers | `@patch("apps.events.tasks.enqueue_static_triggers")` |

---

## Note on Implementation Location

The new implementation will live in a new file (e.g., `apps/chat/channels_v2.py` or a package — TBD when implementation is written). The `new_arch/conftest.py` import paths will be set to point there. No decision needed now; the test plan is location-agnostic.

---

## Verification

Run the full new test suite in isolation:
```bash
uv run pytest apps/channels/tests/new_arch/ -v
```

Run alongside existing tests to confirm no interference:
```bash
uv run pytest apps/channels/tests/ -v
```

Lint and type-check the new test files:
```bash
uv run ruff check apps/channels/tests/new_arch/ --fix
uv run ty check apps/channels/tests/new_arch/
```

**Switchover gate:** All tests in `apps/channels/tests/new_arch/` pass → safe to remove `apps/chat/channels.py`, old test files, and update `apps/channels/tasks.py` imports.
