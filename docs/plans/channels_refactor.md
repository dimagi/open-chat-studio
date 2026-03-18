# Channel Refactoring: Context-Based Stateless Processing Architecture Analysis

**Date**: 2025-12-17
**Status**: Architecture Analysis
**Approach**: Context object \+ stateless processing stages (pipeline/filter pattern)

## Proposed Architecture

- **Context Class**: Holds all data state throughout processing
- **Stateless Stages**: Process and augment the context
- **Core Stages vs Terminal Stages**: Core stages run sequentially and can be short-circuited; terminal stages always run
- **Exception-Based Early Exit**: Any core stage can raise `EarlyExitResponse` to short-circuit remaining core stages
- **Pipeline Orchestrator**: The pipeline catches the exception and feeds the response into terminal stages

This is essentially a **Pipeline Pattern** with exception-based short-circuiting and a guaranteed terminal phase.

## Current Code Analysis

### Message Processing Flow (lines 352-408)

The current `new_user_message()` already follows a semi-linear flow:

```py
def new_user_message(self, message: BaseMessage) -> ChatMessage:
    # 1. Initialize
    self._is_user_message = True
    self._add_message(message)  # Sets self.message, validates participant

    # 2. Ensure session exists
    self._ensure_sessions_exists()

    # 3. Check message type support
    if not self.is_message_type_supported():
        return self._handle_unsupported_message()

    # 4. Handle consent flow (conditional)
    if self._should_handle_pre_conversation_requirements():
        resp = self._handle_pre_conversation_requirements()
        if resp:
            return ChatMessage(content=resp)

    # 5. Extract user query (handles voice transcription if needed)
    user_query = self.user_query  # cached_property that calls _extract_user_query()

    # 6. Get bot response
    ai_message = self._get_bot_response(message=user_query)

    # 7. Format and send response
    files = ai_message.get_attached_files() or []
    self.send_message_to_user(bot_message=ai_message.content, files=files)

    # 8. Update session activity
    self._update_session_activity()

    return ai_message
```

**Key Observation**: This is already a pipeline\! Each step augments state and the next step depends on previous results.

### State Currently Scattered Across

**Instance Variables**:

- `self.experiment`, `self.experiment_channel`, `self.experiment_session`
- `self.message`, `self._participant_identifier`, `self._is_user_message`

**Cached Properties** (lazy computed state):

- `self.messaging_service` (line 196-198)
- `self.bot` (line 201-204)
- `self.user_query` (line 332-337)
- `self.participant_data` (line 227-232)

**Method Returns** (ephemeral state):

- Participant validation result
- Session resolution result
- Bot response
- Formatted message

## Proposed Architecture: Context-Based Pipeline

### 1\. Context Object

```py
@dataclass
class MessageProcessingContext:
    """Mutable context passed through processing stages"""

    # Input (set at creation)
    message: BaseMessage
    experiment: Experiment
    experiment_channel: ExperimentChannel

    # Services (injected at creation)
    messaging_adapter: MessagingAdapter
    trace_service: TracingService

    # Channel-specific dependencies (injected at creation, accessed by stages)
    callbacks: ChannelCallbacks | None = None
    sender: ChannelSender | None = None
    capabilities: ChannelCapabilities | None = None

    # State (built up during processing)
    participant_identifier: str | None = None
    participant_allowed: bool = False
    experiment_session: ExperimentSession | None = None
    bot: Bot | None = None

    # Extracted data
    user_query: str | None = None
    transcript: str | None = None  # For voice messages

    # Bot response
    bot_response: ChatMessage | None = None
    formatted_message: str | None = None
    files_to_send: list[File] = field(default_factory=list)

    # Control flow — set by the pipeline orchestrator when it catches
    # an EarlyExitResponse exception OR when the catch-all error handler
    # generates an error message. Stages do NOT set this directly;
    # they raise EarlyExitResponse instead.
    early_exit_response: str | None = None

    # Sending error — set by ResponseSendingStage when a send fails.
    # SendingErrorHandlerStage reads this to handle platform-specific
    # side effects (e.g., Telegram 403 consent revocation).
    sending_exception: Exception | None = None

    # Error tracking — each stage handles its own errors internally
    # and appends to this list for observability
    processing_errors: list[str] = field(default_factory=list)
```

> **Review decisions applied**:
> - **Exception-based early exit**: Stages raise `EarlyExitResponse` to short-circuit. The pipeline catches it, sets `ctx.early_exit_response`, then runs terminal stages. Stages never check or set this field directly.
> - **Catch-all error handling**: The pipeline also catches unexpected exceptions from core stages. It generates an error message via `EventBot` (preserving the `ChatException` distinction for more specific prompts), falls back to `DEFAULT_ERROR_RESPONSE_TEXT` if generation fails, sets `ctx.early_exit_response`, runs terminal stages, then **re-raises** the original exception so the caller knows processing failed.
> - **Core vs terminal stages**: Core stages (validation, session, consent, query, bot, formatting) can be short-circuited. Terminal stages (ResponseSendingStage, SendingErrorHandlerStage, EarlyExitResponseStage, ActivityTrackingStage) always run.
> - **Callbacks/sender/capabilities on context**: Stages are zero-arg constructors. They access channel-specific dependencies via `ctx.callbacks`, `ctx.sender`, and `ctx.capabilities`. This simplifies pipeline construction and makes stages fully reusable.
> - **No `stage_timings`**: Observability is handled by trace spans in `ProcessingStage.__call__`, not manual timing.
> - **Error handling per-stage**: Each stage handles its own errors internally. `processing_errors` is an observability list, not a pipeline-level error control mechanism.
> - **`sending_exception`**: Single field (not a list). `ResponseSendingStage` sets this when a send fails. `SendingErrorHandlerStage` inspects it for platform-specific side effects.

**Design Choice: Immutable vs Mutable**

Option A (Immutable): Stages return new context

```py
def process(self, ctx: MessageProcessingContext) -> MessageProcessingContext:
    return dataclasses.replace(ctx, user_query="extracted text", ...)
```

Option B (Mutable): Stages modify context in place

```py
def process(self, ctx: MessageProcessingContext) -> None:
    ctx.user_query = "extracted text"
    ctx.participant_allowed = True
```

**Recommendation**: Start with **Option B (Mutable)** for simplicity, can refactor to immutable later if needed.

### 2\. EarlyExitResponse Exception

```py
class EarlyExitResponse(Exception):
    """Raised by any core stage to short-circuit the pipeline.

    The pipeline orchestrator catches this, stores the message on
    ctx.early_exit_response, and then runs terminal stages.
    """

    def __init__(self, response: str):
        self.response = response
        super().__init__(response)
```

### 3\. Processing Stage Interface

```py
class ProcessingStage(ABC):
    """Base class for stateless processing stages.

    Stages are zero-arg: all dependencies come from the context object.
    Each stage handles its own errors internally.

    Stages do NOT check early_exit_response — the pipeline orchestrator
    handles short-circuiting. To exit early, raise EarlyExitResponse.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        """Determine if this stage should run based on context state.

        Default: always run. Override to add stage-specific conditions
        (e.g., "only run if user_query is set").

        NOTE: This is for stage-specific preconditions only. Early exit
        short-circuiting is handled by the pipeline, not by stages.
        """
        return True

    @abstractmethod
    def process(self, ctx: MessageProcessingContext) -> None:
        """Process the context, modifying it in place.

        Raise EarlyExitResponse to short-circuit the pipeline.
        """
        pass

    def __call__(self, ctx: MessageProcessingContext) -> None:
        """Execute stage inside a trace span if conditions are met"""
        if not self.should_run(ctx):
            return
        stage_name = self.__class__.__name__
        with ctx.trace_service.span(stage_name, inputs={}) as span:
            self.process(ctx)
            span.set_outputs({})
```

> **Review decisions applied**:
> - **`should_run` does NOT check `early_exit_response`**: Early exit short-circuiting is the pipeline's responsibility. `should_run` is only for stage-specific preconditions (e.g., "only run if user_query is set").
> - **`EarlyExitResponse` exception**: Stages raise this to short-circuit. The pipeline catches it, stores the message on `ctx.early_exit_response`, then runs terminal stages.
> - **Trace spans for observability**: `__call__` wraps `process` in a trace span, not manual `time.monotonic()` timing. Integrates with existing tracing infrastructure.
> - **Zero-arg stages**: No `__init__` parameters — stages get everything from `ctx`.

### 4\. Concrete Stages

Map current flow to stages:

#### Stage 1: ParticipantValidationStage

```py
class ParticipantValidationStage(ProcessingStage):
    """Validates participant is allowed to interact"""

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.participant_identifier = ctx.message.participant_id

        if ctx.experiment.is_public:
            ctx.participant_allowed = True
        else:
            ctx.participant_allowed = ctx.experiment.is_participant_allowed(
                ctx.participant_identifier
            )

        if not ctx.participant_allowed:
            raise EarlyExitResponse("Sorry, you are not allowed to chat to this bot")
```

**Maps to**: `_participant_is_allowed()` (line 378-381)

#### Stage 2: SessionResolutionStage

```py
class SessionResolutionStage(ProcessingStage):
    """Loads or creates experiment session. Also handles /reset command."""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.participant_allowed

    def process(self, ctx: MessageProcessingContext) -> None:
        # Handle /reset command — end current session so a new one is created
        if self._is_reset_command(ctx):
            self._handle_reset(ctx)

        # Check for pre-set session (Web/Slack channels set this at creation)
        if ctx.experiment_session:
            return  # Already have session

        # Try to load existing session (with select_related for performance)
        existing_session = (
            ExperimentSession.objects
            .select_related("experiment", "participant")
            .filter(
                experiment=ctx.experiment.get_working_version(),
                participant__identifier=ctx.participant_identifier,
            )
            .exclude(status__in=STATUSES_FOR_COMPLETE_CHATS)
            .first()
        )

        if existing_session:
            ctx.experiment_session = existing_session
        else:
            ctx.experiment_session = self._create_session(ctx)

    def _is_reset_command(self, ctx: MessageProcessingContext) -> bool:
        return ctx.message.message_text and ctx.message.message_text.strip() == "/reset"

    def _handle_reset(self, ctx: MessageProcessingContext) -> None:
        """End current session and raise early exit with reset confirmation."""
        if ctx.experiment_session:
            ctx.experiment_session.end(reason="reset by user")
        elif not ctx.experiment_session:
            # Load existing to end it
            existing = ExperimentSession.objects.filter(
                experiment=ctx.experiment.get_working_version(),
                participant__identifier=ctx.participant_identifier,
            ).exclude(status__in=STATUSES_FOR_COMPLETE_CHATS).first()
            if existing:
                existing.end(reason="reset by user")

        raise EarlyExitResponse("Session reset. Send a new message to start over.")
```

**Maps to**: `_ensure_sessions_exists()` (line 697-726) and `/reset` handling

> **Review decisions applied**:
> - **`/reset` inside SessionResolutionStage** (Decision 7): The reset command is fundamentally about session lifecycle, so it belongs here rather than as a separate stage.
> - **Pre-set session** (Decision 4): Web and Slack channels may pre-set `ctx.experiment_session` at creation time. This stage respects that and skips lookup.
> - **`select_related`** (Decision 13): Session query uses `select_related("experiment", "participant")` to avoid N+1 queries downstream.

#### Stage 3: MessageTypeValidationStage

```py
class MessageTypeValidationStage(ProcessingStage):
    """Validates message type is supported by channel"""

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.message.content_type not in ctx.capabilities.supported_message_types:
            raise EarlyExitResponse(self._generate_unsupported_message_response(ctx))
```

**Maps to**: `is_message_type_supported()` and `_handle_unsupported_message()` (lines 789-804)

> Zero-arg: reads `supported_message_types` from `ctx.capabilities`.

#### Stage 4: SessionActivationStage

```py
class SessionActivationStage(ProcessingStage):
    """Activates the session when conversational consent is not required.

    When consent is disabled or no consent form is configured, this stage
    transitions the session directly to ACTIVE so downstream stages can
    proceed. This keeps the side effect out of ConsentFlowStage.should_run.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        if ctx.experiment_session is None:
            return False
        return (
            not ctx.experiment.conversational_consent_enabled
            or not ctx.experiment.consent_form_id
        )

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.experiment_session.update_status(SessionStatus.ACTIVE)
```

**Maps to**: The non-consent path previously handled inside `ConsentFlowStage.should_run()`.

> **Review decision applied**: Session activation for non-consent experiments is a separate stage so that `ConsentFlowStage.should_run()` remains a pure precondition check with no side effects (Decision 16).

#### Stage 5: ConsentFlowStage

```py
class ConsentFlowStage(ProcessingStage):
    """Handles conversational consent state machine.

    This stage only manages consent state transitions and raises
    EarlyExitResponse. It does NOT:
    - Send messages (ResponseSendingStage handles that)
    - Persist to chat history (EarlyExitResponseStage handles that)

    Sub-behaviors:
    1. Seed message: Raise EarlyExitResponse with consent prompt
       when session is in SETUP status
    2. State transitions: SETUP → PENDING → PENDING_PRE_SURVEY → ACTIVE

    This stage is always included in the pipeline — should_run() handles
    the skip logic based on ctx.capabilities.supports_conversational_consent.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        # Skip if channel doesn't support conversational consent
        if not ctx.capabilities.supports_conversational_consent:
            return False

        return (
            ctx.experiment.conversational_consent_enabled and
            ctx.experiment.consent_form_id and
            ctx.experiment_session.status in [
                SessionStatus.SETUP,
                SessionStatus.PENDING,
                SessionStatus.PENDING_PRE_SURVEY,
            ]
        )

    def process(self, ctx: MessageProcessingContext) -> None:
        # State machine: SETUP → PENDING → PENDING_PRE_SURVEY → ACTIVE
        session = ctx.experiment_session
        response = None

        if session.status == SessionStatus.SETUP:
            response = self._ask_for_consent(ctx)
            session.update_status(SessionStatus.PENDING)

        elif session.status == SessionStatus.PENDING:
            if self._user_gave_consent(ctx):
                if not ctx.experiment.pre_survey:
                    response = self._start_conversation(ctx)
                    session.update_status(SessionStatus.ACTIVE)
                else:
                    response = self._ask_for_survey(ctx)
                    session.update_status(SessionStatus.PENDING_PRE_SURVEY)
            else:
                response = self._ask_for_consent(ctx)

        elif session.status == SessionStatus.PENDING_PRE_SURVEY:
            if self._user_gave_consent(ctx):
                response = self._start_conversation(ctx)
                session.update_status(SessionStatus.ACTIVE)
            else:
                response = self._ask_for_survey(ctx)

        if response is not None:
            raise EarlyExitResponse(response)
```

**Maps to**: `_handle_pre_conversation_requirements()` (lines 409-441)

> **Review decisions applied**:
> - **`supports_conversational_consent` on ChannelCapabilities** (not a ClassVar on ChannelBase): `should_run` checks `ctx.capabilities.supports_conversational_consent`. This stage is always included in the pipeline — the skip logic is internal.
> - **Sub-behaviors documented** (Decision 9): State transitions and seed message are this stage's responsibility. Chat history persistence and message sending are handled downstream by `EarlyExitResponseStage` and `ResponseSendingStage` respectively.
> - **Exception-based early exit**: This stage raises `EarlyExitResponse` instead of setting a field. The pipeline catches it and runs terminal stages.
> - **No message sending**: This stage only raises `EarlyExitResponse`. The `ResponseSendingStage` is the sole stage that sends messages to the user.

#### Stage 6: QueryExtractionStage

```py
class QueryExtractionStage(ProcessingStage):
    """Extracts user query from message (handles voice transcription)"""

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.message.content_type == MESSAGE_TYPES.VOICE:
            ctx.user_query = self._transcribe_voice(ctx)
        else:
            ctx.user_query = ctx.message.message_text

    def _transcribe_voice(self, ctx: MessageProcessingContext) -> str:
        # Callbacks receive only the data they need (recipient, transcript, message)
        ctx.callbacks.transcription_started(ctx.participant_identifier)

        audio_file = ctx.callbacks.get_message_audio(ctx.message)
        transcript = self._transcribe_audio(ctx, audio_file)

        if ctx.experiment.echo_transcript:
            ctx.callbacks.echo_transcript(ctx.participant_identifier, transcript)

        ctx.callbacks.transcription_finished(ctx.participant_identifier, transcript)
        return transcript
```

**Maps to**: `_extract_user_query()` and `_get_voice_transcript()` (lines 496-499, 664-673)

> Zero-arg: callbacks accessed via `ctx.callbacks` (injected by ChannelBase at context creation).

#### Stage 7: ChatMessageCreationStage

```py
class ChatMessageCreationStage(ProcessingStage):
    """Creates the ChatMessage DB record for the user's message.

    Separated from query extraction so that the DB record exists before
    bot interaction — this ensures the message is persisted even if the
    bot call fails.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.chat_message = ChatMessage.objects.create(
            chat=ctx.experiment_session.chat,
            message_type=ChatMessageType.HUMAN,
            content=ctx.user_query,
        )
```

**Maps to**: `_create_chat_message_from_user_message()` (line 486-495)

> **Review decision applied** (Decision 6): Separate ChatMessageCreationStage ensures the DB record is created as its own concern, independent of query extraction and bot interaction.

#### Stage 8: BotInteractionStage

```py
class BotInteractionStage(ProcessingStage):
    """Gets response from bot"""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        # Channel-specific "typing" indicator
        ctx.callbacks.submit_input_to_llm(ctx.participant_identifier)

        bot = get_bot(
            ctx.experiment_session,
            ctx.experiment,
            ctx.trace_service
        )

        # Process input
        ctx.bot_response = bot.process_input(
            ctx.user_query,
            attachments=ctx.message.attachments
        )

        # Extract files
        ctx.files_to_send = ctx.bot_response.get_attached_files() or []
```

**Maps to**: `submit_input_to_llm()` and `_get_bot_response()` (lines 269-271, 685-687)

> Zero-arg: callbacks accessed via `ctx.callbacks`.

#### Stage 9: ResponseFormattingStage

```py
class ResponseFormattingStage(ProcessingStage):
    """Formats bot response for channel (handles citations, voice, etc.)"""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.bot_response is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        message = ctx.bot_response.content
        files = ctx.files_to_send

        # Determine if voice or text reply
        should_reply_voice = self._should_use_voice_reply(ctx)

        if should_reply_voice:
            # Strip URLs and emojis for voice
            message, extracted_urls = strip_urls_and_emojis(message)
            ctx.formatted_message = message
            ctx.voice_audio = self._synthesize_voice(ctx, message)
            # URLs will be sent as text after voice
            if extracted_urls or files:
                ctx.additional_text_message = self._format_links_and_files(
                    extracted_urls, files
                )
        else:
            # Text reply - format citations
            message, uncited_files = self._format_reference_section(
                message, files, ctx.capabilities
            )
            message = self._append_attachment_links(message, uncited_files)
            ctx.formatted_message = message

        # Separate files into supported/unsupported for channel
        if ctx.capabilities.supports_files:
            ctx.files_to_send, ctx.unsupported_files = (
                self._split_by_support(files, ctx.capabilities)
            )
```

**Maps to**: `send_message_to_user()` and `_format_reference_section()` (lines 501-546, 548-616)

> Zero-arg: capabilities accessed via `ctx.capabilities`.

#### Stage 10: ResponseSendingStage (Terminal)

```py
class ResponseSendingStage(ProcessingStage):
    """Sends response to user via channel.

    TERMINAL STAGE — runs first among terminal stages.
    This is the ONLY stage that sends messages to the user.
    Handles both normal bot responses and early exit responses.

    Wrapper methods (_send_text, _send_voice) are decorated with
    @notify_on_delivery_failure for in-app notifications on failure.
    The outer try/except catches any exception that propagates past
    the decorator, sets ctx.sending_exception, and never re-raises.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.formatted_message is not None or ctx.early_exit_response is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        try:
            if ctx.early_exit_response:
                self._send_text(ctx, ctx.early_exit_response, ctx.participant_identifier)
                return

            # Normal path — send formatted bot response
            if ctx.voice_audio:
                self._send_voice(ctx, ctx.voice_audio, ctx.participant_identifier)
                if ctx.additional_text_message:
                    self._send_text(ctx, ctx.additional_text_message, ctx.participant_identifier)
            else:
                self._send_text(ctx, ctx.formatted_message, ctx.participant_identifier)

            # Send supported files — each file handles its own error
            for file in ctx.files_to_send:
                try:
                    ctx.sender.send_file(file, ctx.participant_identifier, ctx.experiment_session.id)
                except Exception as e:
                    ctx.processing_errors.append(f"Failed to send file: {e}")
                    link = file.download_link(ctx.experiment_session.id)
                    self._send_text(ctx, link, ctx.participant_identifier)
        except Exception as e:
            ctx.sending_exception = e
            ctx.processing_errors.append(f"Send failed: {e}")

    @notify_on_delivery_failure(context="text message")
    def _send_text(self, ctx: MessageProcessingContext, text: str, recipient: str) -> None:
        ctx.sender.send_text(text, recipient)

    @notify_on_delivery_failure(context="voice message")
    def _send_voice(self, ctx: MessageProcessingContext, audio, recipient: str) -> None:
        ctx.sender.send_voice(audio, recipient)
```

The `@notify_on_delivery_failure` decorator is updated to be context-aware — it reads
`experiment`, `session`, and `platform_title` from the `ctx` parameter instead of
`self.experiment` / `self.experiment_channel`:

```py
def notify_on_delivery_failure(context: str):
    """Decorator that catches exceptions from send methods, creates a
    failure notification, then re-raises.

    Updated for pipeline stages: reads experiment/session/platform from
    the ctx parameter (first positional arg after self).
    """
    def decorator(method):
        @wraps(method)
        def wrapper(self, ctx, *args, **kwargs):
            try:
                return method(self, ctx, *args, **kwargs)
            except Exception as e:
                logger.exception(e)
                message_delivery_failure_notification(
                    ctx.experiment,
                    session=ctx.experiment_session,
                    platform_title=ctx.experiment_channel.platform_enum.title(),
                    context=context,
                )
                raise
        return wrapper
    return decorator
```

**Maps to**: `send_text_to_user()`, `send_voice_to_user()`, `send_file_to_user()` (various lines)

> **Review decisions applied**:
> - **Single sending stage**: `ResponseSendingStage` is the ONLY stage that sends messages. All other stages set `early_exit_response` and let this stage handle delivery.
> - **Decorated wrappers**: `_send_text` and `_send_voice` wrap `ctx.sender` calls with `@notify_on_delivery_failure` for in-app notifications. The decorator is updated to read experiment/session/platform from `ctx` instead of `self`.
> - **Resilient**: Outer try/except catches any exception that propagates past the decorator, sets `ctx.sending_exception`, and never re-raises. This ensures subsequent terminal stages still run.
> - **`send_file` receives `session_id`**: Extra parameter for download link generation.

#### Stage 11: SendingErrorHandlerStage (Terminal)

```py
class SendingErrorHandlerStage(ProcessingStage):
    """Handles platform-specific side effects from send failures.

    TERMINAL STAGE — runs after ResponseSendingStage.
    Inspects ctx.sending_exception for platform-specific errors
    that require action beyond logging (e.g., Telegram 403 "bot
    was blocked" → revoke participant consent).

    Non-actionable exceptions are ignored (already logged by
    ResponseSendingStage).
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.sending_exception is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        self._handle_exception(ctx, ctx.sending_exception)

    def _handle_exception(self, ctx: MessageProcessingContext, exc: Exception) -> None:
        """Handle platform-specific sending exceptions."""
        from telebot.apihelper import ApiTelegramException

        if isinstance(exc, ApiTelegramException):
            if exc.error_code == 403 and "bot was blocked by the user" in exc.description:
                try:
                    participant_data = ParticipantData.objects.get(
                        participant__identifier=ctx.participant_identifier,
                        experiment=ctx.experiment,
                    )
                    participant_data.update_consent(False)
                except ParticipantData.DoesNotExist:
                    ctx.processing_errors.append(
                        "Participant data not found during consent revocation"
                    )
        # Other platform-specific exception handling can be added here
```

> **Review decision**: Dedicated terminal stage for platform-specific send error side effects. Telegram 403 "bot was blocked" triggers consent revocation. This exception is not re-raised — it's already been logged and notified by `ResponseSendingStage`.

#### Stage 12: EarlyExitResponseStage (Terminal)

```py
class EarlyExitResponseStage(ProcessingStage):
    """Persists early_exit_response to chat history.

    TERMINAL STAGE — runs after ResponseSendingStage and
    SendingErrorHandlerStage.

    When a core stage raises EarlyExitResponse, or the pipeline's
    catch-all error handler generates an error message, the pipeline
    sets ctx.early_exit_response. This stage creates the corresponding
    ChatMessage DB record so the response appears in the conversation
    history.

    Persists regardless of whether sending succeeded — chat history
    serves as an audit trail.

    Uses _add_to_history (extracted from the old ConsentFlowStage)
    to persist the message.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.early_exit_response is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.experiment_session:
            self._add_to_history(ctx, ctx.early_exit_response)

    def _add_to_history(self, ctx: MessageProcessingContext, content: str) -> None:
        """Persist the early exit response as an AI message in chat history."""
        ChatMessage.objects.create(
            chat=ctx.experiment_session.chat,
            message_type=ChatMessageType.AI,
            content=content,
        )
```

> **Review decisions applied**:
> - Early exit responses are persisted via this dedicated terminal stage. The `_add_to_history` logic was moved out of `ConsentFlowStage` so that consent (and all other early-exit stages) only raise `EarlyExitResponse` — they never persist or send messages directly.
> - **Persists regardless of sending exceptions**: Even if `ResponseSendingStage` failed to deliver the message, the response is still persisted to chat history for audit purposes.
> - **Runs after sending**: Placed after `ResponseSendingStage` and `SendingErrorHandlerStage` because the pipeline's catch-all error handler can also produce an `early_exit_response`, and `ResponseSendingStage` may produce a `sending_exception` — both should be settled before persistence.

#### Stage 13: ActivityTrackingStage (Terminal)

```py
class ActivityTrackingStage(ProcessingStage):
    """Updates session activity timestamp and experiment versions"""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.experiment_session is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        session = ctx.experiment_session
        update_fields = ["last_activity_at"]
        session.last_activity_at = timezone.now()

        # Track experiment version if versioned
        if ctx.experiment.is_a_version:
            version_number = ctx.experiment.version_number
            current_versions = session.experiment_versions or []
            if version_number not in current_versions:
                session.experiment_versions = current_versions + [version_number]
                update_fields.append("experiment_versions")

        session.save(update_fields=update_fields)
```

**Maps to**: `_update_session_activity()` (lines 860-876)

### 5\. Pipeline Orchestrator

```py
class MessageProcessingPipeline:
    """Orchestrates message processing through core and terminal stages.

    Core stages run sequentially and can be short-circuited by raising
    EarlyExitResponse. Terminal stages always run — they handle both
    normal responses and early exit responses.

    The pipeline is the ONLY place that handles EarlyExitResponse and
    unexpected exceptions. Individual stages never check
    ctx.early_exit_response.

    Error handling has two tiers:
    1. EarlyExitResponse — intentional short-circuit by a stage
    2. Unexpected Exception — catch-all generates an error message
       via EventBot (or falls back to DEFAULT_ERROR_RESPONSE_TEXT),
       runs terminal stages, then RE-RAISES so the caller knows
       processing failed.
    """

    def __init__(
        self,
        core_stages: list[ProcessingStage],
        terminal_stages: list[ProcessingStage],
    ):
        self.core_stages = core_stages
        self.terminal_stages = terminal_stages

    def process(self, ctx: MessageProcessingContext) -> MessageProcessingContext:
        """Run core stages, catch exceptions, then run terminal stages.

        1. Run core stages sequentially. If any raises EarlyExitResponse,
           remaining core stages are skipped.
        2. If any raises an unexpected exception, generate an error message
           and set ctx.early_exit_response.
        3. Run terminal stages unconditionally (they always fire).
        4. If there was an unexpected exception, re-raise it after terminal
           stages complete.
        """
        unexpected_exception = None

        try:
            for stage in self.core_stages:
                stage(ctx)
        except EarlyExitResponse as e:
            ctx.early_exit_response = e.response
        except Exception as e:
            unexpected_exception = e
            ctx.early_exit_response = self._generate_error_message(ctx, e)
            ctx.processing_errors.append(str(e))

        # Terminal stages always run — regardless of early exit or error
        for stage in self.terminal_stages:
            stage(ctx)

        # Re-raise unexpected exceptions after terminal stages complete
        if unexpected_exception is not None:
            raise unexpected_exception

        return ctx

    def _generate_error_message(self, ctx: MessageProcessingContext, exception: Exception) -> str:
        """Generate a user-facing error message using EventBot.

        Preserves the ChatException distinction: ChatException instances
        get a more specific prompt that includes the error message.
        Falls back to DEFAULT_ERROR_RESPONSE_TEXT if EventBot fails.

        Maps to the old _inform_user_of_error() but WITHOUT sending —
        sending is ResponseSendingStage's responsibility.
        """
        trace_info = TraceInfo(name="error", metadata={"error": str(exception)})
        prompt = (
            "Tell the user that something went wrong while processing their message"
            " and that they should try again later."
        )
        if isinstance(exception, ChatException):
            prompt = (
                f"Tell the user that you were unable to process their message and that "
                f"they should try again later or adjust the message type or contents "
                f"according to the following error message: {exception}"
            )

        event_bot = EventBot(
            ctx.experiment_session, ctx.experiment, trace_info, trace_service=ctx.trace_service
        )
        try:
            return event_bot.get_user_message(prompt)
        except Exception:
            logger.exception(
                "Failed to generate error message via EventBot, falling back to default"
            )
            return DEFAULT_ERROR_RESPONSE_TEXT
```

> **Review decisions applied**:
> - The pipeline orchestrator is the sole owner of control flow. Core stages raise `EarlyExitResponse` to short-circuit; the pipeline catches it and feeds the response into terminal stages.
> - **Catch-all error handling**: Unexpected exceptions from core stages trigger error message generation via `EventBot`, preserving the `ChatException` distinction for more specific prompts. Falls back to `DEFAULT_ERROR_RESPONSE_TEXT` if generation fails.
> - **Re-raise after terminal stages**: The original exception is re-raised after terminal stages complete, so the caller (webhook view / Celery task) knows processing failed. Terminal stages (ResponseSendingStage, SendingErrorHandlerStage, EarlyExitResponseStage, ActivityTrackingStage) always run first.
> - **No sending in error handler**: `_generate_error_message` only generates the message. `ResponseSendingStage` handles delivery. This cleanly separates concerns and means the web channel (which never sends) still gets the error message returned via `new_user_message`'s return value.

### 6\. Channel Implementation

Channels become **pipeline builders** \+ **dependency providers**:

```py
@dataclass(frozen=True)
class ChannelCapabilities:
    """Declares what a channel supports. Frozen for safety."""
    supports_voice: bool = False
    supports_files: bool = False
    supports_conversational_consent: bool = True
    supported_message_types: list = field(default_factory=list)
    can_send_file: Callable = lambda file: False


class ChannelBase(ABC):
    """Base channel with pipeline architecture.

    Channel-specific dependencies (callbacks, sender, capabilities) are
    injected into the context object — stages never receive them via __init__.
    """

    # Class-level configuration
    voice_replies_supported: ClassVar[bool] = False
    supported_message_types: ClassVar[list] = []

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
        messaging_adapter: MessagingAdapter | None = None,
        trace_service: TracingService | None = None,
    ):
        self.experiment = experiment
        self.experiment_channel = experiment_channel
        self.experiment_session = experiment_session
        self.messaging_adapter = messaging_adapter or self._create_default_adapter()
        self.trace_service = trace_service or TracingService.create_for_experiment(experiment)

        # Build pipeline (can be overridden by subclasses)
        self.pipeline = self._build_pipeline()

    def _build_pipeline(self) -> MessageProcessingPipeline:
        """Build standard processing pipeline.

        All stages are zero-arg — they get dependencies from the context.
        Core stages can be short-circuited by EarlyExitResponse.
        Terminal stages always run.
        """
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                SessionResolutionStage(),
                MessageTypeValidationStage(),
                SessionActivationStage(),
                ConsentFlowStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                ResponseSendingStage(),
                SendingErrorHandlerStage(),
                EarlyExitResponseStage(),
                ActivityTrackingStage(),
            ],
        )

    def new_user_message(self, message: BaseMessage) -> ChatMessage:
        """Main entry point - runs message through pipeline"""

        # Create context with all channel-specific deps injected
        ctx = MessageProcessingContext(
            message=message,
            experiment=self.experiment,
            experiment_channel=self.experiment_channel,
            experiment_session=self.experiment_session,
            messaging_adapter=self.messaging_adapter,
            trace_service=self.trace_service,
            callbacks=self._get_callbacks(),
            sender=self._get_sender(),
            capabilities=self._get_capabilities(),
        )

        # Process through pipeline (core stages may raise EarlyExitResponse;
        # the pipeline catches it and runs terminal stages regardless)
        pipeline = self._build_pipeline()
        with self.trace_service.trace(
            trace_name=self.experiment.name,
            session=self.experiment_session,
            inputs={"input": message.model_dump()},
        ) as span:
            ctx = pipeline.process(ctx)

            # Determine response to return to caller
            if ctx.early_exit_response:
                response = ChatMessage(content=ctx.early_exit_response)
            elif ctx.bot_response:
                response = ctx.bot_response
            else:
                response = ChatMessage(content="", message_type=ChatMessageType.AI)

            span.set_outputs({"response": response.content})
            return response

    # Abstract methods for channel-specific behavior
    @abstractmethod
    def _create_default_adapter(self) -> MessagingAdapter:
        """Create messaging adapter for this channel"""
        pass

    @abstractmethod
    def _get_callbacks(self) -> 'ChannelCallbacks':
        """Get channel-specific callbacks"""
        pass

    @abstractmethod
    def _get_sender(self) -> 'ChannelSender':
        """Get channel-specific sender"""
        pass

    def _get_capabilities(self) -> 'ChannelCapabilities':
        """Get channel capabilities.

        Override for channels where capabilities depend on the service
        provider at runtime (e.g., WhatsApp file support varies by provider).
        """
        return ChannelCapabilities(
            supports_voice=self.voice_replies_supported,
            supported_message_types=self.supported_message_types,
            supports_files=getattr(self, 'supports_multimedia', False),
            supports_conversational_consent=True,
        )
```

> **Review decisions applied**:
> - **ChannelCapabilities dataclass**: `supports_conversational_consent` lives here (not as a ClassVar on ChannelBase). `frozen=True` for safety.
> - **Zero-arg stages**: Pipeline construction is trivial — no callbacks/sender/capabilities passed to stage constructors.
> - **Dependencies on context**: `new_user_message` injects callbacks, sender, and capabilities into the context object.
> - **Core vs terminal stages**: 9 core stages (can be short-circuited by `EarlyExitResponse`) + 4 terminal stages (always run): ResponseSendingStage → SendingErrorHandlerStage → EarlyExitResponseStage → ActivityTrackingStage.
> - **SessionActivationStage before ConsentFlowStage**: Handles non-consent session activation as a dedicated stage. `ConsentFlowStage.should_run()` is a pure precondition check with no side effects.
> - **ConsentFlowStage always included**: No conditional in `_build_pipeline`. The stage's `should_run()` handles the skip logic.
> - **Dynamic `_get_capabilities()`** (Decision 3): Method can be overridden for channels where capabilities depend on runtime state (e.g., provider-dependent file support).
> - **Channel-specific pipelines**: Channels like `ApiChannel`, `EvaluationChannel`, and `CommCareConnectChannel` override `_build_pipeline` to customize their stage lists (e.g., omitting `ResponseSendingStage` for channels that don't send, adding `CommCareConsentCheckStage` after `SessionResolutionStage`).

### 7\. Concrete Channel Example

```py
class TelegramChannel(ChannelBase):
    voice_replies_supported = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]
    supports_multimedia = True

    def __init__(self, experiment, experiment_channel, experiment_session=None):
        # Initialize Telegram bot
        self.telegram_bot = TeleBot(
            experiment_channel.extra_data["bot_token"],
            threaded=False
        )

        super().__init__(experiment, experiment_channel, experiment_session)

    def _create_default_adapter(self) -> MessagingAdapter:
        return TelegramAdapter(self.telegram_bot)

    def _get_callbacks(self) -> 'ChannelCallbacks':
        return TelegramCallbacks(self.telegram_bot, self.experiment_session)

    def _get_sender(self) -> 'ChannelSender':
        return TelegramSender(self.telegram_bot)

    def _get_capabilities(self) -> 'ChannelCapabilities':
        return ChannelCapabilities(
            supports_voice=True,
            supports_files=True,
            supports_conversational_consent=True,
            supported_message_types=[MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE],
            can_send_file=lambda f: True,  # Telegram supports all file types
        )
```

**Much simpler\!** Channel-specific behavior is isolated to callbacks, sender, and capabilities — not spread throughout the base class. Stages are zero-arg, so `_build_pipeline()` rarely needs overriding.

### 8\. Channel-Specific Pipeline Overrides

Some channels need custom pipelines:

```py
class ApiChannel(ChannelBase):
    """API channel — request/response, no message sending."""

    def _build_pipeline(self) -> MessageProcessingPipeline:
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                SessionResolutionStage(),
                MessageTypeValidationStage(),
                SessionActivationStage(),
                ConsentFlowStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                # No ResponseSendingStage or SendingErrorHandlerStage —
                # API returns the response directly via new_user_message()
                EarlyExitResponseStage(),
                ActivityTrackingStage(),
            ],
        )

    def _get_sender(self) -> ChannelSender:
        # Not used — no sending stage in pipeline
        return NoOpSender()


class EvaluationChannel(ChannelBase):
    """Evaluation channel — internal, no message sending."""

    def _build_pipeline(self) -> MessageProcessingPipeline:
        # Similar to ApiChannel — omit sending stages
        return MessageProcessingPipeline(
            core_stages=[...],  # Same core stages
            terminal_stages=[
                EarlyExitResponseStage(),
                ActivityTrackingStage(),
            ],
        )


class CommCareConnectChannel(ChannelBase):
    """CommCare Connect — adds platform-specific consent check."""

    def _build_pipeline(self) -> MessageProcessingPipeline:
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                SessionResolutionStage(),
                CommCareConsentCheckStage(),  # Platform-specific consent
                MessageTypeValidationStage(),
                SessionActivationStage(),
                ConsentFlowStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                ResponseSendingStage(),
                SendingErrorHandlerStage(),
                EarlyExitResponseStage(),
                ActivityTrackingStage(),
            ],
        )


class CommCareConsentCheckStage(ProcessingStage):
    """Checks CommCare Connect platform-specific consent.

    Separate from ConsentFlowStage — this checks system_metadata["consent"]
    on ParticipantData, not the conversational consent state machine.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.experiment_session is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        try:
            participant_data = ParticipantData.objects.get(
                participant__identifier=ctx.participant_identifier,
                experiment=ctx.experiment,
            )
        except ParticipantData.DoesNotExist:
            raise EarlyExitResponse("Participant has not given consent to chat")

        if not participant_data.system_metadata.get("consent", False):
            raise EarlyExitResponse("Participant has not given consent to chat")
```

> **Review decision applied**: Channels customize their pipelines by overriding `_build_pipeline()`. This is cleaner than capability flags for structural differences (e.g., "don't send at all" vs "send differently"). `ApiChannel` and `EvaluationChannel` omit sending stages entirely. `CommCareConnectChannel` inserts a platform-specific consent stage.

## Advantages of Context-Based Architecture

### 1\. **Separation of Concerns** ✅

Each stage has one responsibility:

- ParticipantValidation: Only checks participant
- SessionResolution: Only deals with sessions
- ConsentFlow: Only handles consent state machine

### 2\. **Testability** ✅✅✅

**Huge win** \- can test each stage independently with stubs (no DB):

```py
def test_participant_validation_allows_public_experiment():
    ctx = _make_context(experiment=Mock(is_public=True))

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is True

def test_participant_validation_blocks_private_experiment():
    ctx = _make_context(
        experiment=Mock(is_public=False, is_participant_allowed=Mock(return_value=False)),
    )

    with pytest.raises(EarlyExitResponse) as exc_info:
        ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is False
    assert "not allowed" in exc_info.value.response
```

Fast, DB-free, no complex setup \- just test the stage logic with stubs\!

### 3\. **Explicit State** ✅

Context makes all state explicit and traceable:

- What was the input?
- What was computed at each stage?
- Why did processing stop early? (`EarlyExitResponse` exception with message)
- Stage performance visible via trace spans

### 4\. **Easy to Extend** ✅

Add new stages without modifying existing ones:

```py
class RateLimitingStage(ProcessingStage):
    """Check if user has exceeded rate limit"""

    def should_run(self, ctx) -> bool:
        return ctx.participant_allowed

    def process(self, ctx) -> None:
        if self._is_rate_limited(ctx.participant_identifier):
            raise EarlyExitResponse("Too many messages. Please wait.")
```

Just insert into pipeline's `core_stages` list \- no changes to other stages\!

### 5\. **Channel-Specific Customization** ✅

Channels can:

- Add core stages: `pipeline.core_stages.insert(0, CustomStage())`
- Add terminal stages: `pipeline.terminal_stages.append(CustomCleanupStage())`
- Remove stages: `pipeline.core_stages = [s for s in stages if not isinstance(s, ConsentFlowStage)]`
- Replace stages: `pipeline.core_stages[2] = CustomValidationStage()`

### 6\. **Observability** ✅

Each stage automatically runs inside a trace span (via `ProcessingStage.__call__`):

```py
with ctx.trace_service.span(stage_name, inputs={}) as span:
    self.process(ctx)
    span.set_outputs({})
```

Integrates with existing tracing infrastructure — no manual timing code needed.

### 7\. **Stateless \= Easier to Reason About** ✅

Stages don't maintain state between messages:

- No `self.user_query` that might be stale
- No `self.experiment_session` that might be wrong
- No `cached_property` that might cache wrong value
- Just: input context → process → output context

### 8\. **Clear Control Flow** ✅

Exception-based early exit makes the flow obvious:

- Core stages raise `EarlyExitResponse` — no checking flags in every stage
- Pipeline orchestrator is the single point of control flow management
- Terminal stages always run — guaranteed cleanup and response delivery
- No risk of stages forgetting to check a flag

## Challenges & Solutions

### Challenge 1: Channel-Specific Callbacks

**Problem**: Stages like QueryExtractionStage need channel-specific behavior:

- `transcription_started()` \- Telegram shows "uploading voice" indicator
- `echo_transcript()` \- Some channels echo back the transcript
- `submit_input_to_llm()` \- Telegram shows "typing" indicator

**Solution A: Callbacks Object (with targeted parameters)**

```py
class ChannelCallbacks:
    """Channel-specific callback hooks.
    All methods are no-ops by default. Methods that target a user
    receive `recipient: str` — not the full context.
    """
    def transcription_started(self, recipient: str) -> None:
        pass  # Default: no-op

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        pass  # Default: no-op

    def submit_input_to_llm(self, recipient: str) -> None:
        pass  # Default: no-op

    def get_message_audio(self, message: 'BaseMessage') -> BytesIO:
        raise NotImplementedError("Channel must implement audio retrieval")

class TelegramCallbacks(ChannelCallbacks):
    def __init__(self, telegram_bot):
        self.bot = telegram_bot

    def transcription_started(self, recipient):
        self.bot.send_chat_action(recipient, "upload_voice")

    def submit_input_to_llm(self, recipient):
        self.bot.send_chat_action(recipient, "typing")
```

Stages access callbacks via context, passing only the data each method needs:

```py
class QueryExtractionStage(ProcessingStage):
    # Zero-arg — no __init__ needed

    def process(self, ctx):
        ctx.callbacks.transcription_started(ctx.participant_identifier)
        # ... transcribe
```

**Solution B: Context-Based** Add callback methods to context:

```py
@dataclass
class MessageProcessingContext:
    # ... other fields

    # Callback functions (injected)
    on_transcription_started: Callable[[], None] = lambda: None
    on_echo_transcript: Callable[[str], None] = lambda t: None
    on_submit_to_llm: Callable[[], None] = lambda: None
```

**Decision**: Use **Solution A (Callbacks Object)** with targeted parameters, injected into the context. Callback methods receive only the data they need (e.g., `recipient: str`) rather than the full context. This avoids the chicken-and-egg problem (callbacks don't need `participant_identifier` at construction time — they receive it at call time), keeps stages zero-arg, and maintains a clean, testable interface with no-op defaults.

### Challenge 2: Early Exit Handling

**Problem**: Some stages need to exit early (participant not allowed, unsupported message, consent flow).

**Decision**: Exception-based early exit via `EarlyExitResponse`:

```py
raise EarlyExitResponse("Appropriate message")
```

Any core stage can raise this exception. The pipeline orchestrator catches it, stores the response on `ctx.early_exit_response`, then runs terminal stages. This approach:

- Eliminates the need for every stage to check a flag in `should_run()`
- Makes the pipeline orchestrator the single point of control flow
- Guarantees terminal stages always run (cleanup, response delivery, activity tracking)
- Uses Python's natural exception flow — no risk of forgetting to check a flag

### Challenge 3: Service Injection

**Problem**: Stages need access to services (messaging\_adapter, trace\_service, etc.)

**Solution**: Pass services in context:

```py
@dataclass
class MessageProcessingContext:
    messaging_adapter: MessagingAdapter
    trace_service: TracingService
    # ... rest
```

Stages can access via `ctx.messaging_adapter.send_text(...)`.

### Challenge 4: Session Mutations

**Problem**: Some stages need to mutate session (update status, save changes).

**Current Solution**: Context holds session reference, stages mutate it:

```py
ctx.experiment_session.update_status(SessionStatus.ACTIVE)
ctx.experiment_session.save()
```

**Better Solution** (future refinement): Use command pattern:

```py
@dataclass
class SessionCommand:
    type: Literal["update_status", "save"]
    # ... parameters

ctx.session_commands.append(SessionCommand(type="update_status", ...))
```

Execute commands after pipeline completes. More complex, but allows rollback on error.

**Recommendation**: Start with direct mutation, refine if needed.

### Challenge 5: Bot Instance Creation

**Problem**: Bot instance is expensive to create, should be lazy and cached.

**Solution**: Create bot on-demand in BotInteractionStage:

```py
def process(self, ctx):
    if not ctx.bot:
        ctx.bot = get_bot(ctx.experiment_session, ctx.experiment, ctx.trace_service)

    ctx.bot_response = ctx.bot.process_input(ctx.user_query)
```

Or use property on context:

```py
@property
def bot(self) -> Bot:
    if not self._bot:
        self._bot = get_bot(self.experiment_session, self.experiment, self.trace_service)
    return self._bot
```

**Recommendation**: Lazy creation in stage \- simpler and explicit.

## Comparison with Your Previous Proposals

### vs Inheritance Proposal (Mixins)

**Inheritance**:

- ❌ Complex MRO (Method Resolution Order) with multiple mixins
- ❌ Hard to test mixins in isolation
- ❌ Tight coupling between base class and mixins
- ✅ Familiar pattern for Django developers

**Context-Based**:

- ✅ Clear linear flow, easy to understand
- ✅ Each stage independently testable
- ✅ Loose coupling via context interface
- ❌ Less familiar pattern

**Winner**: Context-Based for testability and clarity

### vs Composition Proposal (Registry \+ Adapters)

**Composition**:

- ✅ Good separation via Adapters
- ✅ Registry for capability discovery
- ❌ Still has stateful ChannelBase with complex logic
- ❌ Capabilities \+ Adapters \+ Strategies \= many abstractions

**Context-Based**:

- ✅ Inherits Adapter pattern benefits
- ✅ Simpler \- just Context \+ Stages \+ Pipeline
- ✅ Can add Registry later if needed
- ✅ Stages are simpler than Strategies

**Winner**: Context-Based for simplicity

### vs Implementation Plan (Incremental Adapters)

**Incremental**:

- ✅ Low-risk phased approach
- ✅ Backward compatible
- ❌ Still leaves complex ChannelBase unchanged
- ❌ Doesn't address core architectural issues

**Context-Based**:

- ❌ Bigger refactor, higher initial effort
- ✅ Fundamentally improves architecture
- ✅ Can still be done incrementally (see below)
- ✅ End result is much cleaner

**Winner**: Depends on risk tolerance. Context-Based has better end state.

## Migration Strategy: Incremental Context-Based Refactor

You can adopt context-based architecture incrementally:

### Phase 1: Add Context Class (No Breaking Changes)

```py
# Still in apps/chat/channels.py
class MessageProcessingContext:
    # ... definition

class ChannelBase(ABC):
    def new_user_message(self, message: BaseMessage) -> ChatMessage:
        # Create context
        ctx = self._create_context(message)

        # Call OLD code but pass context around
        return self._new_user_message_with_context(ctx)

    def _new_user_message_with_context(self, ctx):
        # Gradually refactor existing code to use ctx instead of self
        # For now, just copy state back and forth
        self.message = ctx.message
        # ... existing code
        ctx.bot_response = result
        return result
```

**Validation**: Tests still pass, no behavior change.

### Phase 2: Extract First Stage

Extract simplest stage (e.g., ParticipantValidation):

```py
class ParticipantValidationStage:
    def process(self, ctx):
        # ... logic

class ChannelBase:
    def _new_user_message_with_context(self, ctx):
        # Use stage
        ParticipantValidationStage().process(ctx)
        if not ctx.participant_allowed:
            return ...

        # Rest of existing code
```

**Validation**: Tests pass, participant validation now in stage.

### Phase 3: Extract More Stages Incrementally

One by one:

- SessionResolutionStage
- MessageTypeValidationStage
- ConsentFlowStage
- etc.

Each time:

- Extract logic to stage
- Update \_new\_user\_message\_with\_context to use stage
- Run tests

### Phase 4: Introduce Pipeline

Once most stages extracted:

```py
class ChannelBase:
    def new_user_message(self, message):
        ctx = self._create_context(message)
        pipeline = self._build_pipeline()
        ctx = pipeline.process(ctx)
        return self._extract_response(ctx)
```

**Validation**: All tests pass, flow now through pipeline.

## Testing Strategy

> **Review decisions applied**:
> - **DB-free stage unit tests** (Decision 9): Use stubs/mocks for stage unit tests, not `ExperimentFactory` (which hits the DB). Stages should be testable as pure logic.
> - **Phased test migration** (Decision 10): Migrate tests alongside code — as each stage is extracted, write new stage tests and update/remove corresponding old tests.
> - **Edge case checklist** (Decision 11): Maintain a checklist of edge cases per stage (see below).
> - **Citation tests as pure unit tests** (Decision 12): `_format_reference_section` tests should be pure unit tests with string inputs — no DB, no factories.

### Unit Tests for Stages (DB-Free)

Stage unit tests use stub objects — no `@pytest.mark.django_db`, no factories:

```py
# tests/channels/stages/test_participant_validation.py
import pytest
from unittest.mock import Mock

def _make_context(**overrides):
    """Helper to create a stub context for stage testing."""
    defaults = dict(
        message=Mock(participant_id="test-user", message_text="hello"),
        experiment=Mock(is_public=True, is_participant_allowed=Mock(return_value=False)),
        experiment_channel=Mock(),
        messaging_adapter=Mock(),
        trace_service=Mock(),
        callbacks=Mock(),
        sender=Mock(),
        capabilities=Mock(
            supports_conversational_consent=True,
            supported_message_types=["text"],
        ),
        participant_allowed=False,
        participant_identifier=None,
        experiment_session=None,
        processing_errors=[],
    )
    defaults.update(overrides)
    return Mock(**defaults)

def test_allows_participant_in_public_experiment():
    ctx = _make_context(experiment=Mock(is_public=True))

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is True

def test_blocks_participant_in_private_experiment():
    ctx = _make_context(
        experiment=Mock(is_public=False, is_participant_allowed=Mock(return_value=False)),
    )

    with pytest.raises(EarlyExitResponse) as exc_info:
        ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is False
    assert "not allowed" in exc_info.value.response
```

**No DB needed\!** Pure logic testing with stubs. Fast and isolated.

### Integration Tests for Pipeline

Integration tests use factories (hit DB) to test stage interactions:

```py
@pytest.mark.django_db
def test_full_pipeline_happy_path(mock_bot):
    experiment = ExperimentFactory(is_public=True)
    channel = ExperimentChannelFactory(experiment=experiment)
    ctx = MessageProcessingContext(
        experiment=experiment,
        experiment_channel=channel,
        message=BaseMessage(
            participant_id="allowed_user",
            message_text="Hello"
        ),
        messaging_adapter=MockAdapter(),
        trace_service=MockTraceService(),
        callbacks=ChannelCallbacks(),  # No-op defaults
        sender=MockSender(),
        capabilities=ChannelCapabilities(supported_message_types=["text"]),
    )

    pipeline = MessageProcessingPipeline(
        core_stages=[
            ParticipantValidationStage(),
            SessionResolutionStage(),
            # ... remaining core stages
        ],
        terminal_stages=[
            ResponseSendingStage(),
            SendingErrorHandlerStage(),
            EarlyExitResponseStage(),
            ActivityTrackingStage(),
        ],
    )
    result_ctx = pipeline.process(ctx)

    assert result_ctx.participant_allowed
    assert result_ctx.experiment_session is not None
```

### Integration Tests for Channels (End-to-End)

```py
@pytest.mark.django_db
def test_telegram_channel_processes_message(experiment, telegram_channel):
    channel = TelegramChannel(experiment, telegram_channel)
    message = TelegramMessage.parse(telegram_update)

    response = channel.new_user_message(message)

    assert response.content
    # Check side effects (session created, messages sent, etc.)
```

### Citation / Response Formatting Tests (Pure Unit Tests)

```py
# No @pytest.mark.django_db — pure string-in, string-out
def test_format_reference_section_with_citations():
    message = "The answer is foo [1]."
    files = [Mock(name="doc.pdf", download_link=Mock(return_value="/files/doc.pdf"))]
    capabilities = ChannelCapabilities(supports_files=True)

    result, uncited = ResponseFormattingStage._format_reference_section(
        message, files, capabilities
    )

    assert "[1]" not in result  # Citation replaced with link
    assert "doc.pdf" in result
```

### Edge Case Test Checklist

Each stage should have tests for these edge cases (where applicable):

- **ParticipantValidationStage**: Public experiment, private+allowed, private+blocked, missing participant_id
- **SessionResolutionStage**: Existing session found, no session (create new), pre-set session (Web/Slack), /reset with active session, /reset with no session, session in completed status
- **MessageTypeValidationStage**: Supported type, unsupported type, empty message
- **SessionActivationStage**: Consent disabled (activates), no consent form (activates), consent enabled with form (skips), no session (skips)
- **ConsentFlowStage**: Each status transition (SETUP→PENDING, PENDING→ACTIVE, PENDING→PENDING_PRE_SURVEY, PENDING_PRE_SURVEY→ACTIVE), consent denied, channel without consent support
- **QueryExtractionStage**: Text message, voice message, voice transcription failure
- **ChatMessageCreationStage**: Normal creation, session without chat
- **BotInteractionStage**: Successful response, bot error, response with files, response without files
- **ResponseFormattingStage**: Text response, voice response, citations, no citations, unsupported files
- **ResponseSendingStage**: Early exit response sent, normal text send, voice send, file send success, file send failure (fallback to link), no response at all (no-op), send failure sets `ctx.sending_exception`, delivery failure notification created on error
- **SendingErrorHandlerStage**: Telegram 403 "bot blocked" (revokes consent), non-Telegram exception (no-op), no sending exception (skips)
- **EarlyExitResponseStage**: Early exit with session (persists), early exit without session (no-op), no early exit (skips), persists even when sending_exception is set
- **ActivityTrackingStage**: Session update, versioned experiment tracking
- **Pipeline catch-all**: Unexpected exception generates error message, `ChatException` gets specific prompt, `EventBot` failure falls back to `DEFAULT_ERROR_RESPONSE_TEXT`, terminal stages run, original exception re-raised

## Implementation Roadmap (Based on Your Choices)

### Phase 1: Foundation (Week 1-2)

**Goal**: Add context and infrastructure without breaking existing code

**1.1 Create Context Class** (`apps/chat/channel_context.py`):

```py
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.channels.models import ExperimentChannel
    from apps.channels.datamodels import BaseMessage

@dataclass
class MessageProcessingContext:
    """Context for message processing pipeline"""

    # Input (set at creation)
    message: 'BaseMessage'
    experiment: 'Experiment'
    experiment_channel: 'ExperimentChannel'
    experiment_session: 'ExperimentSession | None' = None

    # Services (injected)
    messaging_adapter: 'MessagingAdapter | None' = None
    trace_service: 'TracingService | None' = None

    # Channel-specific dependencies (injected at creation)
    callbacks: 'ChannelCallbacks | None' = None
    sender: 'ChannelSender | None' = None
    capabilities: 'ChannelCapabilities | None' = None

    # State (populated during processing)
    participant_identifier: str | None = None
    participant_allowed: bool = False
    bot: 'Bot | None' = None
    user_query: str | None = None
    transcript: str | None = None
    bot_response: 'ChatMessage | None' = None
    formatted_message: str | None = None
    voice_audio: 'SynthesizedAudio | None' = None
    files_to_send: list = field(default_factory=list)
    unsupported_files: list = field(default_factory=list)

    # Control flow — set by the pipeline when it catches EarlyExitResponse.
    # Stages do NOT set this directly; they raise EarlyExitResponse instead.
    early_exit_response: str | None = None

    # Error tracking
    processing_errors: list[str] = field(default_factory=list)
```

**1.2 Create EarlyExitResponse Exception** (`apps/chat/channel_exceptions.py`):

```py
class EarlyExitResponse(Exception):
    """Raised by any core stage to short-circuit the pipeline."""

    def __init__(self, response: str):
        self.response = response
        super().__init__(response)
```

**1.3 Create Channel Abstractions** (`apps/chat/channel_abstractions.py`):

```py
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

@dataclass(frozen=True)
class ChannelCapabilities:
    """Declares what a channel supports. Frozen for safety."""
    supports_voice: bool = False
    supports_files: bool = False
    supports_conversational_consent: bool = True
    supported_message_types: list = field(default_factory=list)
    can_send_file: Callable = lambda file: False


class ChannelCallbacks:
    """Base class for channel-specific callback hooks.
    All methods are no-ops by default — channels override only what they need.
    Methods that target a user receive `recipient: str` (not the full context).
    """

    def transcription_started(self, recipient: str) -> None:
        pass

    def transcription_finished(self, recipient: str, transcript: str) -> None:
        pass

    def echo_transcript(self, recipient: str, transcript: str) -> None:
        pass

    def submit_input_to_llm(self, recipient: str) -> None:
        pass

    def get_message_audio(self, message: 'BaseMessage') -> BytesIO:
        raise NotImplementedError("Channel must implement audio retrieval")


class ChannelSender(ABC):
    """Abstract base for sending messages to users.

    Sender implementations encapsulate platform-specific sending details
    (e.g., from_number, bot token, thread_ts) at construction time.
    The send methods receive only the data that varies per call.
    """

    @abstractmethod
    def send_text(self, text: str, recipient: str) -> None: ...

    @abstractmethod
    def send_voice(self, audio: 'SynthesizedAudio', recipient: str) -> None: ...

    @abstractmethod
    def send_file(self, file: 'File', recipient: str, session_id: int) -> None: ...
```

**1.3 Update ChannelBase** (still in `apps/chat/channels.py`):

```py
class ChannelBase(ABC):
    def __init__(self, experiment, experiment_channel, experiment_session=None,
                 messaging_adapter=None, trace_service=None):
        # ... existing init code

    def new_user_message(self, message: BaseMessage) -> ChatMessage:
        """Main entry point - gradually migrating to context-based"""
        # Create context with channel-specific deps injected
        ctx = MessageProcessingContext(
            message=message,
            experiment=self.experiment,
            experiment_channel=self.experiment_channel,
            experiment_session=self.experiment_session,
            messaging_adapter=self.messaging_adapter,
            trace_service=self.trace_service,
            callbacks=self._get_callbacks(),
            sender=self._get_sender(),
            capabilities=self._get_capabilities(),
        )

        # For now, delegate to existing code but pass context
        return self._new_user_message_internal(ctx)

    def _new_user_message_internal(self, ctx: MessageProcessingContext):
        """Temporary bridge - existing code but using context"""
        # Copy context to self for existing code
        self.message = ctx.message
        self._experiment_session = ctx.experiment_session

        # Call existing implementation
        return self._new_user_message()

    # ... rest of existing code unchanged
```

**Validation**: Run all tests \- should pass with zero changes.

**Commit**: "Add MessageProcessingContext, EarlyExitResponse, ChannelCallbacks, ChannelCapabilities, ChannelSender infrastructure"

---

### Phase 2: Extract First Stage (Week 2-3)

**Goal**: Prove the pattern works with simplest stage

**2.1 Create Stage Base** (`apps/chat/channel_stages.py`):

```py
from abc import ABC, abstractmethod

class ProcessingStage(ABC):
    """Base class for stateless, zero-arg processing stages.

    Stages do NOT check early_exit_response — the pipeline handles that.
    To exit early, raise EarlyExitResponse.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        """Default: always run. Override for stage-specific preconditions."""
        return True

    @abstractmethod
    def process(self, ctx: MessageProcessingContext) -> None:
        """Process context, modifying it in place.
        Raise EarlyExitResponse to short-circuit the pipeline."""
        pass

    def __call__(self, ctx: MessageProcessingContext) -> None:
        """Execute stage inside a trace span if should_run."""
        if not self.should_run(ctx):
            return
        stage_name = self.__class__.__name__
        with ctx.trace_service.span(stage_name, inputs={}) as span:
            self.process(ctx)
            span.set_outputs({})
```

**2.2 Extract ParticipantValidationStage**:

```py
class ParticipantValidationStage(ProcessingStage):
    """Validates participant is allowed to interact"""

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.participant_identifier = ctx.message.participant_id

        if ctx.experiment.is_public:
            ctx.participant_allowed = True
            return

        ctx.participant_allowed = ctx.experiment.is_participant_allowed(
            ctx.participant_identifier
        )

        if not ctx.participant_allowed:
            raise EarlyExitResponse("Sorry, you are not allowed to chat to this bot")
```

**2.3 Use Stage in ChannelBase**:

```py
class ChannelBase(ABC):
    def _new_user_message_internal(self, ctx: MessageProcessingContext):
        # NEW: Use stage for participant validation
        try:
            ParticipantValidationStage()(ctx)  # __call__ handles should_run + tracing
        except EarlyExitResponse as e:
            return ChatMessage(content=e.response)

        # Copy to self for existing code
        self.message = ctx.message
        self._experiment_session = ctx.experiment_session
        self._participant_identifier = ctx.participant_identifier

        # Continue with existing code (skip participant check since we did it above)
        # ... existing _new_user_message but remove participant check
```

**2.4 Add Stage Tests** (`apps/chat/tests/test_channel_stages.py`):

```py
# DB-free unit tests — use stubs, not factories
import pytest
from unittest.mock import Mock
from apps.chat.channel_stages import ParticipantValidationStage
from apps.chat.channel_exceptions import EarlyExitResponse

def _make_context(**overrides):
    """Helper to create a stub context for stage testing."""
    defaults = dict(
        message=Mock(participant_id="test-user", message_text="hello"),
        experiment=Mock(is_public=True, is_participant_allowed=Mock(return_value=False)),
        experiment_channel=Mock(),
        trace_service=Mock(),
        callbacks=Mock(),
        sender=Mock(),
        capabilities=Mock(supports_conversational_consent=True, supported_message_types=["text"]),
        participant_allowed=False,
        participant_identifier=None,
    )
    defaults.update(overrides)
    return Mock(**defaults)

def test_participant_validation_allows_public_experiment():
    ctx = _make_context(experiment=Mock(is_public=True))

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is True

def test_participant_validation_blocks_private_experiment():
    ctx = _make_context(
        experiment=Mock(is_public=False, is_participant_allowed=Mock(return_value=False)),
    )

    with pytest.raises(EarlyExitResponse) as exc_info:
        ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is False
    assert "not allowed" in exc_info.value.response
```

**Note**: The whitelisted user test (which needs DB access) belongs in integration tests with `@pytest.mark.django_db` and factories. Stage unit tests should be fast and DB-free.

**Validation**:

- New tests pass (no DB needed)
- All existing tests still pass
- Participant validation logic now in stage

**Commit**: "Extract ParticipantValidationStage with tests"

---

### Phase 3: Extract Core Stages (Week 3-6)

**Goal**: Extract remaining stages one at a time

**3.1 SessionResolutionStage** (Week 3):

```py
class SessionResolutionStage(ProcessingStage):
    """Loads or creates experiment session. Also handles /reset."""

    def should_run(self, ctx) -> bool:
        return ctx.participant_allowed

    def process(self, ctx) -> None:
        # Handle /reset command (raises EarlyExitResponse)
        if self._is_reset_command(ctx):
            self._handle_reset(ctx)

        # Respect pre-set session (Web/Slack)
        if ctx.experiment_session:
            return

        # Try to load existing (with select_related for performance)
        ctx.experiment_session = self._load_latest_session(ctx)

        # Create new if needed
        if not ctx.experiment_session:
            ctx.experiment_session = self._create_new_session(ctx)
```

**Tests**: Test session loading, creation, /reset with active session, /reset with no session, pre-set session

**Commit**: "Extract SessionResolutionStage with /reset handling"

**3.2 MessageTypeValidationStage** (Week 4):

```py
class MessageTypeValidationStage(ProcessingStage):
    # Zero-arg — reads supported types from ctx.capabilities

    def process(self, ctx) -> None:
        if ctx.message.content_type not in ctx.capabilities.supported_message_types:
            raise EarlyExitResponse(self._generate_error_message(ctx))
```

**Tests**: Test supported/unsupported message types

**Commit**: "Extract MessageTypeValidationStage"

**3.3 SessionActivationStage** (Week 4): Simple stage — activates session when consent is not required.

```py
class SessionActivationStage(ProcessingStage):
    """Activates the session when conversational consent is not required."""

    def should_run(self, ctx) -> bool:
        if ctx.experiment_session is None:
            return False
        return (
            not ctx.experiment.conversational_consent_enabled
            or not ctx.experiment.consent_form_id
        )

    def process(self, ctx) -> None:
        ctx.experiment_session.update_status(SessionStatus.ACTIVE)
```

**Tests**: Test activation when consent disabled, no consent form, consent enabled (skips), no session (skips)

**Commit**: "Extract SessionActivationStage"

**3.4 ConsentFlowStage** (Week 4-5): Most complex stage \- consent state machine.

```py
class ConsentFlowStage(ProcessingStage):
    # Zero-arg — uses ctx.capabilities for consent support check

    def should_run(self, ctx) -> bool:
        if not ctx.capabilities.supports_conversational_consent:
            return False
        return (
            ctx.experiment.conversational_consent_enabled and
            ctx.experiment.consent_form_id and
            ctx.experiment_session.status in [
                SessionStatus.SETUP,
                SessionStatus.PENDING,
                SessionStatus.PENDING_PRE_SURVEY,
            ]
        )

    def process(self, ctx) -> None:
        # State machine logic from _handle_pre_conversation_requirements
        # Raises EarlyExitResponse for consent prompts
        # ...
```

**Tests**: Test each consent state transition, channel without consent

**Commit**: "Extract ConsentFlowStage with state machine"

**3.5 QueryExtractionStage** (Week 5):

```py
class QueryExtractionStage(ProcessingStage):
    # Zero-arg — uses ctx.callbacks for voice hooks

    def process(self, ctx) -> None:
        if ctx.message.content_type == MESSAGE_TYPES.VOICE:
            ctx.user_query = self._transcribe_voice(ctx)
        else:
            ctx.user_query = ctx.message.message_text
```

**Tests**: Test text extraction and voice transcription

**Commit**: "Extract QueryExtractionStage"

**3.6 ChatMessageCreationStage** (Week 5):

```py
class ChatMessageCreationStage(ProcessingStage):
    """Creates the ChatMessage DB record for the user's message."""

    def should_run(self, ctx) -> bool:
        return ctx.user_query is not None

    def process(self, ctx) -> None:
        ctx.chat_message = ChatMessage.objects.create(
            chat=ctx.experiment_session.chat,
            message_type=ChatMessageType.HUMAN,
            content=ctx.user_query,
        )
```

**Tests**: Test message creation, session without chat

**Commit**: "Extract ChatMessageCreationStage"

**3.7 BotInteractionStage** (Week 6):

```py
class BotInteractionStage(ProcessingStage):
    # Zero-arg — uses ctx.callbacks for typing indicator

    def should_run(self, ctx) -> bool:
        return ctx.user_query is not None

    def process(self, ctx) -> None:
        ctx.callbacks.submit_input_to_llm(ctx.participant_identifier)

        if not ctx.bot:
            ctx.bot = get_bot(
                ctx.experiment_session,
                ctx.experiment,
                ctx.trace_service
            )

        ctx.bot_response = ctx.bot.process_input(
            ctx.user_query,
            attachments=ctx.message.attachments
        )

        ctx.files_to_send = ctx.bot_response.get_attached_files() or []
```

**Tests**: Test bot interaction with mocked bot

**Commit**: "Extract BotInteractionStage"

**3.8 ResponseFormattingStage** (Week 6):

```py
class ResponseFormattingStage(ProcessingStage):
    # Zero-arg — uses ctx.capabilities for formatting decisions

    def should_run(self, ctx) -> bool:
        return ctx.bot_response is not None

    def process(self, ctx) -> None:
        # Logic from send_message_to_user and _format_reference_section
        # Uses ctx.capabilities for voice/file decisions
        # Citation tests should be pure unit tests (no DB)
```

**Tests**: Test message formatting, citation handling (pure unit tests), voice/text selection

**Commit**: "Extract ResponseFormattingStage"

**3.9 Terminal Stages** (Week 6):

Extract the four terminal stages. These always run — the pipeline passes them both normal and early-exit contexts.

Order: `ResponseSendingStage` → `SendingErrorHandlerStage` → `EarlyExitResponseStage` → `ActivityTrackingStage`

```py
class ResponseSendingStage(ProcessingStage):
    """TERMINAL STAGE: Sends response to user. Wraps all sends in try/except.
    On failure: creates delivery failure notification, sets ctx.sending_exception.
    """
    # ... (see section 4 for full implementation)

class SendingErrorHandlerStage(ProcessingStage):
    """TERMINAL STAGE: Handles platform-specific send error side effects.
    E.g., Telegram 403 "bot blocked" → revoke participant consent.
    """
    # ... (see section 4 for full implementation)

class EarlyExitResponseStage(ProcessingStage):
    """TERMINAL STAGE: Persists early_exit_response to chat history.
    Persists regardless of sending exceptions.
    Uses _add_to_history (moved out of ConsentFlowStage).
    """

    def should_run(self, ctx) -> bool:
        return ctx.early_exit_response is not None

    def process(self, ctx) -> None:
        if ctx.experiment_session:
            self._add_to_history(ctx, ctx.early_exit_response)

class ActivityTrackingStage(ProcessingStage):
    """TERMINAL STAGE: Updates session activity timestamp and experiment versions."""
    # ... (see section 4 for full implementation)
```

**Tests**: Test each terminal stage independently (see edge case checklist)

**Commit**: "Extract terminal stages (ResponseSending, SendingErrorHandler, EarlyExitResponse, ActivityTracking)"

---

### Phase 4: Introduce Pipeline (Week 7\)

**Goal**: Wire stages together with pipeline orchestrator

**4.1 Create Pipeline** (`apps/chat/channel_pipeline.py`):

```py
class MessageProcessingPipeline:
    """Orchestrates message processing through core and terminal stages.

    Core stages can be short-circuited by EarlyExitResponse.
    Unexpected exceptions generate an error message, run terminal stages,
    then re-raise. Terminal stages always run.
    """

    def __init__(
        self,
        core_stages: list[ProcessingStage],
        terminal_stages: list[ProcessingStage],
    ):
        self.core_stages = core_stages
        self.terminal_stages = terminal_stages

    def process(self, ctx: MessageProcessingContext) -> MessageProcessingContext:
        """Run core stages, catch exceptions, then run terminal stages."""
        unexpected_exception = None

        try:
            for stage in self.core_stages:
                stage(ctx)
        except EarlyExitResponse as e:
            ctx.early_exit_response = e.response
        except Exception as e:
            unexpected_exception = e
            ctx.early_exit_response = self._generate_error_message(ctx, e)
            ctx.processing_errors.append(str(e))

        # Terminal stages always run
        for stage in self.terminal_stages:
            stage(ctx)

        # Re-raise after terminal stages complete
        if unexpected_exception is not None:
            raise unexpected_exception

        return ctx

    def _generate_error_message(self, ctx, exception):
        """Generate user-facing error message. See pipeline orchestrator section."""
        # ... (see section 5 for full implementation)
```

**4.2 Update ChannelBase**:

```py
class ChannelBase(ABC):
    def __init__(self, ...):
        # ... existing init

    def _build_pipeline(self) -> MessageProcessingPipeline:
        """Build default processing pipeline.
        Core stages can be short-circuited by EarlyExitResponse.
        Terminal stages always run.
        """
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                SessionResolutionStage(),
                MessageTypeValidationStage(),
                SessionActivationStage(),
                ConsentFlowStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                ResponseSendingStage(),
                SendingErrorHandlerStage(),
                EarlyExitResponseStage(),
                ActivityTrackingStage(),
            ],
        )

    def new_user_message(self, message: BaseMessage) -> ChatMessage:
        """Process message through pipeline"""
        ctx = MessageProcessingContext(
            message=message,
            experiment=self.experiment,
            experiment_channel=self.experiment_channel,
            experiment_session=self.experiment_session,
            messaging_adapter=self.messaging_adapter,
            trace_service=self.trace_service,
            callbacks=self._get_callbacks(),
            sender=self._get_sender(),
            capabilities=self._get_capabilities(),
        )

        # Run pipeline
        with self.trace_service.trace(...) as span:
            ctx = self.pipeline.process(ctx)

            # Determine return value
            if ctx.early_exit_response:
                response = ChatMessage(content=ctx.early_exit_response)
            elif ctx.bot_response:
                response = ctx.bot_response
            else:
                response = ChatMessage(content="", message_type=ChatMessageType.AI)

            span.set_outputs({"response": response.content})
            return response

    @abstractmethod
    def _get_callbacks(self) -> ChannelCallbacks:
        """Return channel-specific callbacks"""
        pass

    @abstractmethod
    def _get_sender(self) -> 'ChannelSender':
        """Return channel-specific sender"""
        pass

    def _get_capabilities(self) -> 'ChannelCapabilities':
        """Return channel capabilities. Override for provider-dependent channels."""
        return ChannelCapabilities(
            supports_voice=self.voice_replies_supported,
            supported_message_types=self.supported_message_types,
            supports_files=getattr(self, 'supports_multimedia', False),
            supports_conversational_consent=True,
        )
```

**4.3 Implement Channel-Specific Abstractions**:

```py
class TelegramCallbacks(ChannelCallbacks):
    def __init__(self, telegram_bot):
        self.bot = telegram_bot

    def transcription_started(self, recipient):
        self.bot.send_chat_action(recipient, "upload_voice")

    def submit_input_to_llm(self, recipient):
        self.bot.send_chat_action(recipient, "typing")

class TelegramSender(ChannelSender):
    def __init__(self, telegram_bot):
        self.bot = telegram_bot

    def send_text(self, text, recipient):
        self.bot.send_message(recipient, text)

    # ... etc

class TelegramChannel(ChannelBase):
    def _get_callbacks(self) -> ChannelCallbacks:
        return TelegramCallbacks(self.telegram_bot)

    def _get_sender(self) -> ChannelSender:
        return TelegramSender(self.telegram_bot)

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice=True,
            supports_files=True,
            supports_conversational_consent=True,
            supported_message_types=[MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE],
            can_send_file=lambda f: True,
        )
```

**Validation**:

- All tests pass
- Message processing now flows through pipeline
- Stage execution visible via trace spans

**Commit**: "Introduce MessageProcessingPipeline"

---

## Review Decisions Summary

All decisions below were made during the plan review and are reflected throughout this document.

| # | Area | Decision | Summary |
|---|------|----------|---------|
| 1 | Architecture | `EarlyExitResponse` exception | Stages raise `EarlyExitResponse` to short-circuit. Pipeline catches it, sets `ctx.early_exit_response`, runs terminal stages. |
| 2 | Architecture | Core vs terminal stages | 9 core stages (short-circuitable) + 4 terminal stages (always run). Pipeline orchestrator owns control flow. |
| 3 | Architecture | Callbacks/sender/capabilities on context | Stages are zero-arg. Channel-specific dependencies are injected into `ctx` by `ChannelBase.new_user_message()`. |
| 4 | Architecture | Runtime `_get_capabilities()` | Method on ChannelBase, overridable for provider-dependent channels (e.g., WhatsApp file support varies). |
| 5 | Architecture | Pre-set session on context | Web/Slack channels set `ctx.experiment_session` at creation. `SessionResolutionStage` respects this. |
| 6 | Code Quality | Each stage handles own errors | Stages append to `ctx.processing_errors` for observability. |
| 7 | Code Quality | Separate `ChatMessageCreationStage` | Stage between QueryExtraction and BotInteraction. DB record exists before bot call. |
| 8 | Code Quality | `/reset` inside `SessionResolutionStage` | Reset is session lifecycle — belongs in the stage that manages sessions. Raises `EarlyExitResponse`. |
| 9 | Code Quality | ConsentFlowStage sub-behaviors explicit | Docstring documents: state transitions, seed message. Raises `EarlyExitResponse`. |
| 16 | Code Quality | `SessionActivationStage` for non-consent path | Dedicated stage activates session when consent is disabled. Keeps `ConsentFlowStage.should_run()` side-effect-free. |
| 17 | Architecture | Pipeline catch-all error handler | Unexpected exceptions from core stages: generate error message via `EventBot` (preserving `ChatException` distinction), fall back to `DEFAULT_ERROR_RESPONSE_TEXT`, set `ctx.early_exit_response`, run terminal stages, then **re-raise**. |
| 18 | Architecture | Terminal stage order | `ResponseSendingStage` → `SendingErrorHandlerStage` → `EarlyExitResponseStage` → `ActivityTrackingStage`. Sending first, then error handling, then persistence, then tracking. |
| 19 | Architecture | `SendingErrorHandlerStage` (terminal) | Handles platform-specific send error side effects (e.g., Telegram 403 consent revocation). Reads `ctx.sending_exception`. Non-actionable exceptions are ignored. |
| 20 | Architecture | `ResponseSendingStage` resilience | `_send_text` and `_send_voice` wrappers decorated with `@notify_on_delivery_failure` (updated to read from `ctx`). Outer try/except catches any propagated exception, sets `ctx.sending_exception`, never re-raises. |
| 21 | Architecture | `ctx.sending_exception` | Single `Exception | None` field (not a list). Set by `ResponseSendingStage` on send failure. Read by `SendingErrorHandlerStage`. |
| 22 | Architecture | Early exit persistence regardless | `EarlyExitResponseStage` persists to chat history even if `ResponseSendingStage` failed. Chat history is an audit trail. |
| 23 | Architecture | Channel-specific pipelines | `ApiChannel` and `EvaluationChannel` override `_build_pipeline` to omit `ResponseSendingStage`/`SendingErrorHandlerStage`. `CommCareConnectChannel` adds `CommCareConsentCheckStage` after `SessionResolutionStage`. |
| 24 | Architecture | `ChannelSender.send_file` signature | `send_file(file, recipient, session_id)` — session_id as extra param since session may not exist when sender is constructed. |
| 25 | Architecture | No web channel `_inform_user_of_error` override | Web channel no longer needs a no-op override. Error message generation is separated from sending. The generated error message flows back to the web UI via `new_user_message`'s return value. |
| — | Update | `EarlyExitResponseStage` (terminal) | Persists `ctx.early_exit_response` to chat history. Runs after sending stages. Uses `_add_to_history` (moved out of ConsentFlowStage). |
| — | Update | `ResponseSendingStage` (terminal) | Sole stage that sends messages. Handles both normal responses and early exit responses. Wraps all sends in try/except with delivery failure notifications. |
| — | Update | `ActivityTrackingStage` (terminal) | Updates session timestamps. Always runs when session exists. |
| — | Update | Callbacks receive targeted parameters | Callback methods receive `recipient: str` (not full context). No chicken-and-egg problem — `participant_identifier` passed at call time. |
| 10 | Testing | DB-free stage unit tests | Use stubs/mocks (`unittest.mock.Mock`), not factories. Test `EarlyExitResponse` with `pytest.raises`. |
| 11 | Testing | Phased test migration | Migrate tests alongside code — as stages are extracted, write new stage tests and update old ones. |
| 12 | Testing | Edge case test checklist | Per-stage checklist of edge cases to test (see Testing Strategy section). |
| 13 | Testing | Citation tests as pure unit tests | `_format_reference_section` tests use string inputs — no DB, no factories. |
| 14 | Performance | `select_related` on session queries | `SessionResolutionStage` uses `.select_related("experiment", "participant")`. |
| 15 | Performance | Document `select_related` for experiment FKs | Experiment queries throughout stages should use appropriate `select_related`. |
| — | Example feedback | Trace spans in `__call__` | `ProcessingStage.__call__` uses `ctx.trace_service.span()`, not `time.monotonic()` timing. |
| — | Example feedback | `supports_conversational_consent` on `ChannelCapabilities` | Not a ClassVar on ChannelBase. ConsentFlowStage checks `ctx.capabilities.supports_conversational_consent`. |

### Performance Notes (Deferred)

These were noted during review but deferred for later implementation:

- **`count()` → `exists()`**: Where the code checks if any sessions exist, use `.exists()` instead of `.count() > 0`.
- **Bot instance reuse**: Consider caching bot instances across messages within the same session to avoid repeated initialization.
