# Channel Refactor Testing Plan

## Context

We are refactoring `apps/chat/channels.py` into a new stage-based pipeline architecture (reference: `docs/plans/channels_refactor_example.py`). The new implementation lives inside the `apps/channels/` app (not in `apps/chat/`). New tests are written from scratch — existing tests are not touched.

Once all new tests pass, we do a cold-turkey switchover: delete old `apps/chat/channels.py`, update imports to point to `apps/channels/`, and delete old test files.

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
- `ChannelCapabilities` — frozen dataclass (voice, files, consent, static_triggers, message types, `can_send_file` callable)
- `MessageProcessingContext` — mutable dataclass carrying all pipeline state (includes `sending_exception`, `channel_context`)
- `EarlyExitResponse(Exception)` — raised by core stages to short-circuit

**Protocols:**
- `ChannelCallbacks` — no-op base hooks (transcription_started, echo_transcript, submit_input_to_llm, get_message_audio)
- `ChannelSender(ABC)` — send_text, send_voice, send_file (send_file takes session_id as extra param)

**Pipeline:**
- `ProcessingStage(ABC)` — `should_run(ctx)` + `process(ctx)`; callable via `__call__`
- `MessageProcessingPipeline` — runs core stages, catches `EarlyExitResponse` + unexpected exceptions (catch-all generates error message via EventBot, re-raises after terminal stages), always runs terminal stages

**Core stages** (can raise `EarlyExitResponse`):
`ParticipantValidationStage` → `SessionResolutionStage` → `MessageTypeValidationStage` → `SessionActivationStage` → `ConsentFlowStage` → `QueryExtractionStage` → `ChatMessageCreationStage` → `BotInteractionStage` → `ResponseFormattingStage`

**Terminal stages** (always run, in order):
`ResponseSendingStage` → `SendingErrorHandlerStage` → `PersistenceStage` → `ActivityTrackingStage`

**Channel-specific stages:**
- `CommCareConsentCheckStage` — platform-specific consent check (inserted after SessionResolutionStage)
- `EvalsBotInteractionStage` — uses EvalsBot instead of get_bot() (replaces BotInteractionStage)

---

## Test File Structure

All new tests live under `apps/channels/tests/new_arch/` — sibling to existing test files so pytest finds them without config changes.

```text
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
        test_session_activation.py       # SessionActivationStage
        test_consent_flow.py
        test_query_extraction.py
        test_chat_message_creation.py
        test_bot_interaction.py
        test_response_formatting.py
        test_response_sending.py         # ResponseSendingStage (terminal)
        test_sending_error_handler.py    # SendingErrorHandlerStage (terminal)
        test_persistence.py             # PersistenceStage (terminal)
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
        test_api_channel.py
        test_web_channel.py
        test_evaluation_channel.py
        test_commcare_channel.py
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
        self.files_sent = []      # list of (file, recipient, session_id)

    def send_text(self, text, recipient): self.text_messages.append((text, recipient))
    def send_voice(self, audio, recipient): self.voice_messages.append((audio, recipient))
    def send_file(self, file, recipient, session_id): self.files_sent.append((file, recipient, session_id))
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
            supports_static_triggers=True,
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
                 early_exit_response=None, sending_exception=None,
                 channel_context=None, **extra):
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
        sending_exception=sending_exception,
        channel_context=channel_context or {},
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
| Voice message → `voice` tag on ChatMessage (via PersistenceStage) | `test_voice_tag_created_on_message` |
| Voice synthesis failure → notification + text fallback (in ResponseFormattingStage) | `TestNotifications.*` |
| No synthetic voice → text reply | `test_reply_with_text_when_synthetic_voice_not_specified` |
| Non-allowlisted participant → refusal, no session | `test_participant_authorization` |
| Versioned experiment → version tracked on session after each message | `test_incoming_message_adds_version_on_session` |
| New sessions link to working experiment (not a version) | `test_new_sessions_are_linked_to_the_working_experiment` |
| Supported attachments sent directly; unsupported appended as links | `test_supported_and_unsupported_attachments` |
| `GenerationCancelled` → empty ChatMessage, no exception | `test_chat_message_returned_for_cancelled_generate` |
| Failed transcription → user informed | `test_failed_transcription_informs_the_user` |
| Bot error → catch-all generates error message via EventBot, user informed, exception re-raised | (new) |
| Bot error + EventBot fails → DEFAULT_ERROR_RESPONSE_TEXT used | (new) |
| ChatException → specific error prompt via EventBot | (new) |
| Audio synthesis failure → notification + text fallback | `TestNotifications.*` |
| File delivery failure → notification + download link sent | `TestNotifications.*` |
| Transcription failure → notification triggered | `TestNotifications.*` |
| Pre-existing session on context → SessionResolution is no-op (Web/Slack) | new |
| EarlyExitResponse from any core stage → terminal stages still run | new |
| Unexpected exception from core stage → terminal stages run, then re-raised | new |
| `ResponseSendingStage` is the ONLY place *responses* reach the user (callbacks may send indicators/echoes mid-pipeline) | new |
| Send failure → `ctx.sending_exception` set, `SendingErrorHandlerStage` runs | new |
| `NEW_HUMAN_MESSAGE` trigger fires after chat message creation (gated by `supports_static_triggers`) | new |
| Ad hoc bot message (`send_message_to_user`) → mini pipeline runs formatting + sending | `test_ad_hoc_bot_message` |
| Ad hoc bot message with voice config → voice/text decision applies | new |
| Ad hoc bot message with files → supported sent directly, unsupported as links | new |

---

## Layer 2: Pipeline Orchestrator Tests (`test_pipeline_orchestrator.py`)

No DB needed. All stages are `MagicMock` instances.

- All core stages run in order on happy path; terminal stages follow.
- Core stage N raises `EarlyExitResponse` → stages N+1..M skipped; `ctx.early_exit_response` set.
- Core stage raises unexpected exception → `_generate_error_message` called, `ctx.early_exit_response` set, terminal stages run, exception re-raised.
- `_generate_error_message` with `ChatException` → specific prompt used.
- `_generate_error_message` with EventBot failure → `DEFAULT_ERROR_RESPONSE_TEXT` used.
- ALL terminal stages run regardless of early exit or unexpected exception.
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

### `SessionActivationStage`
- `experiment_session is None` → `should_run()` False.
- Consent disabled → activates session to ACTIVE.
- No consent form → activates session to ACTIVE.
- Consent enabled with form → `should_run()` False (skips).

### `ConsentFlowStage` (`@pytest.mark.django_db`)
- `supports_conversational_consent=False` → `should_run()` False.
- Consent not enabled → `should_run()` False.
- No consent form → `should_run()` False.
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
- `supports_static_triggers=True` → `enqueue_static_triggers(NEW_HUMAN_MESSAGE)` called.
- `supports_static_triggers=False` → `enqueue_static_triggers` NOT called.

### `BotInteractionStage`
- `user_query is None` → `should_run()` False.
- `submit_input_to_llm()` called before bot.
- `ctx.bot` is None → bot created lazily; already set → reused.
- Successful call → `ctx.bot_response` and `ctx.files_to_send` set.
- Exceptions propagate (caught by pipeline catch-all, NOT by the stage).

### `ResponseFormattingStage`
- `bot_response is None` → `should_run()` False.
- Voice ALWAYS + voice provider → `ctx.voice_audio` set, URLs in `ctx.additional_text_message`.
- Voice NEVER → text path, `ctx.voice_audio` is None.
- Voice RECIPROCAL: voice input → voice; text input → text.
- `AudioSynthesizeException` → fallback to text, notification created, `ctx.voice_audio` set to None.
- `supports_files=False` → all files in `unsupported_files`.
- `supports_files=True` → files split via `can_send_file`.
- Unsupported files appended as links to `ctx.formatted_message` (text path).
- Voice path + unsupported files → in `ctx.additional_text_message`.

### `ResponseSendingStage` (terminal)
- Both `formatted_message` and `early_exit_response` are None → `should_run()` False.
- Early exit path → `sender.send_text(early_exit_response)`, no voice/file sends.
- Normal text path → `sender.send_text(formatted_message)`.
- Normal voice path → `sender.send_voice()`; `additional_text_message` present → `sender.send_text()` too.
- Voice send fails → notification + fallback text.
- Files in `files_to_send` → `sender.send_file(file, recipient, session_id)` per file.
- File send fails → notification + download link sent via `sender.send_text()`.
- Outer send failure → `ctx.sending_exception` set, delivery failure notification created, exception NOT propagated.
- `@notify_on_delivery_failure` decorator on `_send_text` / `_send_voice` wrappers.

### `SendingErrorHandlerStage` (terminal)
- `sending_exception is None` → `should_run()` False.
- Telegram `ApiTelegramException(403, "bot was blocked")` → participant consent revoked via `ParticipantData.update_consent(False)`.
- Telegram 403 + `ParticipantData.DoesNotExist` → error appended to `ctx.processing_errors`.
- Non-Telegram exception → no-op (already logged by ResponseSendingStage).

### `PersistenceStage` (terminal) (`@pytest.mark.django_db`)
- Both `early_exit_response` and `voice_audio` are None → `should_run()` False.
- `experiment_session is None` → no persistence (early return).
- Early exit present + session → `ChatMessage(AI, content=early_exit_response)` created.
- Early exit persists even when `sending_exception` is set (audit trail).
- Voice audio present + bot_response → bot_response tagged "voice" + audio saved as File attachment.
- Voice audio present but bot_response is None → no voice persistence (no-op).

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
- `ApiTelegramException` → propagates (NOT caught by sender — caught by ResponseSendingStage).
- Voice send → `antiflood(send_voice, audio, duration)`.
- File (`send_file(file, recipient, session_id)`): image→`send_photo`, video→`send_video`, audio→`send_audio`, other→`send_document`.

### `WhatsappSender` (mock `messaging_service`)
- `send_text()` → `service.send_text_message(platform=WHATSAPP)`.
- `send_voice()` → `service.send_voice_message()`.
- `send_file(file, recipient, session_id)` → `service.send_file_to_user(platform=WHATSAPP, download_link=file.download_link(session_id))`.

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
- `_get_capabilities()` → `supports_voice=True, supports_files=True, supports_static_triggers=True, [TEXT, VOICE]`.
- `_can_send_file()`: image <10MB→True; image >10MB→False; video <50MB→True; unknown MIME→False.
- `_build_pipeline()` → default pipeline with correct stage types and count.

### `WhatsappChannel`
- `messaging_service` lazily resolved and cached.
- `_get_capabilities()` delegates all fields from messaging service.
- `_get_sender()` uses `extra_data["number"]` as `from_number`.
- `_get_callbacks()` wraps the sender.

### `ApiChannel`
- `_build_pipeline()` omits `ResponseSendingStage` and `SendingErrorHandlerStage`.
- `_get_sender()` returns `NoOpSender`.
- `_get_capabilities()` → text only, no voice/files.

### `WebChannel`
- Requires pre-existing session (raises if not provided).
- `_build_pipeline()` omits `SessionResolutionStage`, `ConsentFlowStage`, `ResponseSendingStage`, `SendingErrorHandlerStage`.
- `_get_capabilities()` → `supports_conversational_consent=False`.
- `start_new_session()` and `check_and_process_seed_message()` class methods unchanged.

### `EvaluationChannel`
- Requires pre-existing session.
- `_create_context()` sets `channel_context={"participant_data": ...}`.
- `_build_pipeline()` uses `EvalsBotInteractionStage` instead of `BotInteractionStage`, omits sending stages.
- `_get_capabilities()` → `supports_static_triggers=False`.

### `CommCareConnectChannel`
- `_build_pipeline()` inserts `CommCareConsentCheckStage` after `SessionResolutionStage`.
- `CommCareConsentCheckStage`: no consent in `ParticipantData.system_metadata` → raises `EarlyExitResponse`.
- `CommCareConsentCheckStage`: `ParticipantData.DoesNotExist` → raises `EarlyExitResponse`.

### `ChannelBase` utility methods (not pipeline, stay as-is)
- `from_experiment_session()` → returns correct channel subclass for each platform.
- `from_experiment_session()` with unsupported platform → raises exception.
- `get_channel_class_for_platform()` → maps platform strings to channel classes.
- `start_new_session()` with working experiment → session created.
- `start_new_session()` with versioned experiment → raises `VersionedExperimentSessionsNotAllowedException`.
- `ensure_session_exists_for_participant()` → loads or creates session for identifier.
- `ensure_session_exists_for_participant(new_session=True)` → ends existing session, creates new.
- `ensure_session_exists_for_participant()` with mismatched identifier → raises `ChannelException`.

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

## Implementation Location

The new implementation lives inside the `apps/channels/` app. At switchover time, delete `apps/chat/channels.py` and update all `from apps.chat.channels import ...` statements to import from `apps/channels/` instead.

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

**Per-channel gate:** Each channel is migrated in its own PR. The PR adds the new channel + tests and removes the old channel + tests. All tests (new and remaining old) must pass before merge. See the **Incremental Rollout Plan** section in `docs/plans/channels_refactor.md` for the full PR sequence.
