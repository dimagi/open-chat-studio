# Channel Refactoring: Context-Based Stateless Processing Architecture Analysis

**Date**: 2025-12-17
**Status**: Architecture Analysis
**Approach**: Context object \+ stateless processing stages (pipeline/filter pattern)

## Proposed Architecture

- **Context Class**: Holds all data state throughout processing
- **Stateless Stages**: Process and augment the context
- **Conditional Execution**: Stages skip or run based on context state
- **Clear Flow**: Context passes through stages sequentially

This is essentially a **Pipeline Pattern** (aka Chain of Responsibility or Middleware Pattern).

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

    # Control flow — single field for early exit
    # If set, pipeline stops and this becomes the response.
    # Replaces the old dual-flag approach (should_process_message + early_exit_response).
    early_exit_response: str | None = None

    # Error tracking — each stage handles its own errors internally
    # and appends to this list for observability
    processing_errors: list[str] = field(default_factory=list)
```

> **Review decisions applied**:
> - **Single early exit field**: `early_exit_response` is the sole control flow mechanism. When set, the pipeline stops. The old `should_process_message` flag is removed — it was redundant.
> - **Callbacks/sender/capabilities on context**: Stages are zero-arg constructors. They access channel-specific dependencies via `ctx.callbacks`, `ctx.sender`, and `ctx.capabilities`. This simplifies pipeline construction and makes stages fully reusable.
> - **No `stage_timings`**: Observability is handled by trace spans in `ProcessingStage.__call__`, not manual timing.
> - **Error handling per-stage**: Each stage handles its own errors internally. `processing_errors` is an observability list, not a pipeline-level error control mechanism.

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

### 2\. Processing Stage Interface

```py
class ProcessingStage(ABC):
    """Base class for stateless processing stages.

    Stages are zero-arg: all dependencies come from the context object.
    Each stage handles its own errors internally.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        """Determine if this stage should run based on context state.

        Default: run if no early exit has been triggered.
        Override to add additional conditions (always call super first).
        """
        return ctx.early_exit_response is None

    @abstractmethod
    def process(self, ctx: MessageProcessingContext) -> None:
        """Process the context, modifying it in place"""
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
> - **`should_run` checks `early_exit_response is None`** instead of `ctx.should_process_message`. Single control flow field.
> - **Trace spans for observability**: `__call__` wraps `process` in a trace span, not manual `time.monotonic()` timing. Integrates with existing tracing infrastructure.
> - **Zero-arg stages**: No `__init__` parameters — stages get everything from `ctx`.

### 3\. Concrete Stages

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
            ctx.early_exit_response = "Sorry, you are not allowed to chat to this bot"
```

**Maps to**: `_participant_is_allowed()` (line 378-381)

#### Stage 2: SessionResolutionStage

```py
class SessionResolutionStage(ProcessingStage):
    """Loads or creates experiment session. Also handles /reset command."""

    def process(self, ctx: MessageProcessingContext) -> None:
        # Handle /reset command — end current session so a new one is created
        if self._is_reset_command(ctx):
            self._handle_reset(ctx)
            return

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
        """End current session and signal early exit with reset confirmation."""
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

        ctx.early_exit_response = "Session reset. Send a new message to start over."
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
            ctx.early_exit_response = self._generate_unsupported_message_response(ctx)
```

**Maps to**: `is_message_type_supported()` and `_handle_unsupported_message()` (lines 789-804)

> Zero-arg: reads `supported_message_types` from `ctx.capabilities`.

#### Stage 4: ConsentFlowStage

```py
class ConsentFlowStage(ProcessingStage):
    """Handles conversational consent state machine.

    This stage only manages consent state transitions and sets
    early_exit_response. It does NOT:
    - Send messages (ResponseSendingStage handles that)
    - Persist to chat history (EarlyExitResponseStage handles that)

    Sub-behaviors:
    1. Seed message: Set initial consent prompt as early_exit_response
       when session is in SETUP status
    2. State transitions: SETUP → PENDING → PENDING_PRE_SURVEY → ACTIVE
    3. Non-consent path: When consent is not enabled, session transitions
       directly to ACTIVE (no early exit)

    This stage is always included in the pipeline — should_run() handles
    the skip logic based on ctx.capabilities.supports_conversational_consent.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        if ctx.early_exit_response is not None:
            return False

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

        if session.status == SessionStatus.SETUP:
            response = self._ask_for_consent(ctx)
            session.update_status(SessionStatus.PENDING)
            ctx.early_exit_response = response

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

            ctx.early_exit_response = response

        elif session.status == SessionStatus.PENDING_PRE_SURVEY:
            if self._user_gave_consent(ctx):
                response = self._start_conversation(ctx)
                session.update_status(SessionStatus.ACTIVE)
            else:
                response = self._ask_for_survey(ctx)

            ctx.early_exit_response = response
```

**Maps to**: `_handle_pre_conversation_requirements()` (lines 409-441)

> **Review decisions applied**:
> - **`supports_conversational_consent` on ChannelCapabilities** (not a ClassVar on ChannelBase): `should_run` checks `ctx.capabilities.supports_conversational_consent`. This stage is always included in the pipeline — the skip logic is internal.
> - **Sub-behaviors documented** (Decision 8): State transitions and seed message are this stage's responsibility. Chat history persistence and message sending are handled downstream by `EarlyExitResponseStage` and `ResponseSendingStage` respectively.
> - **Single `early_exit_response`**: All `ctx.should_process_message = False` lines removed.
> - **No message sending**: This stage only sets `early_exit_response`. The `ResponseSendingStage` is the sole stage that sends messages to the user.

#### Stage 5: QueryExtractionStage

```py
class QueryExtractionStage(ProcessingStage):
    """Extracts user query from message (handles voice transcription)"""

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.message.content_type == MESSAGE_TYPES.VOICE:
            ctx.user_query = self._transcribe_voice(ctx)
        else:
            ctx.user_query = ctx.message.message_text

    def _transcribe_voice(self, ctx: MessageProcessingContext) -> str:
        # Channel-specific callbacks accessed via context
        ctx.callbacks.transcription_started()

        audio_file = ctx.callbacks.get_message_audio(ctx.message)
        transcript = self._transcribe_audio(ctx, audio_file)

        if ctx.experiment.echo_transcript:
            ctx.callbacks.echo_transcript(transcript)

        ctx.callbacks.transcription_finished(transcript)
        return transcript
```

**Maps to**: `_extract_user_query()` and `_get_voice_transcript()` (lines 496-499, 664-673)

> Zero-arg: callbacks accessed via `ctx.callbacks` (injected by ChannelBase at context creation).

#### Stage 6: ChatMessageCreationStage

```py
class ChatMessageCreationStage(ProcessingStage):
    """Creates the ChatMessage DB record for the user's message.

    Separated from query extraction so that the DB record exists before
    bot interaction — this ensures the message is persisted even if the
    bot call fails.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.early_exit_response is None and ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.chat_message = ChatMessage.objects.create(
            chat=ctx.experiment_session.chat,
            message_type=ChatMessageType.HUMAN,
            content=ctx.user_query,
        )
```

**Maps to**: `_create_chat_message_from_user_message()` (line 486-495)

> **Review decision applied** (Decision 6): Separate ChatMessageCreationStage ensures the DB record is created as its own concern, independent of query extraction and bot interaction.

#### Stage 7: BotInteractionStage

```py
class BotInteractionStage(ProcessingStage):
    """Gets response from bot"""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.early_exit_response is None and ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        # Channel-specific "typing" indicator via callbacks
        ctx.callbacks.submit_input_to_llm()

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

#### Stage 8: ResponseFormattingStage

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

#### Stage 9: EarlyExitResponseStage

```py
class EarlyExitResponseStage(ProcessingStage):
    """Persists early_exit_response to chat history.

    When a stage (e.g., ConsentFlowStage, ParticipantValidationStage)
    sets early_exit_response, this stage creates the corresponding
    ChatMessage DB record so the response appears in the conversation
    history.

    Uses _add_to_history (extracted from the old ConsentFlowStage)
    to persist the message.

    This stage runs only when early_exit_response is set.
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

> **Review decision**: Early exit responses are persisted via this dedicated stage. The `_add_to_history` logic was moved out of `ConsentFlowStage` so that consent (and all other early-exit stages) only set `early_exit_response` — they never persist or send messages directly.

#### Stage 10: ResponseSendingStage

```py
class ResponseSendingStage(ProcessingStage):
    """Sends response to user via channel.

    This is the ONLY stage that sends messages to the user.
    It always fires — handling both normal bot responses and early exit responses.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        # Always fire — we have either a formatted_message or an early_exit_response
        return ctx.formatted_message is not None or ctx.early_exit_response is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.early_exit_response:
            # Early exit path — send the early exit response
            ctx.sender.send_text(ctx.early_exit_response, ctx.participant_identifier)
            return

        # Normal path — send formatted bot response
        if ctx.voice_audio:
            ctx.sender.send_voice(ctx.voice_audio, ctx.participant_identifier)
            if ctx.additional_text_message:
                ctx.sender.send_text(
                    ctx.additional_text_message,
                    ctx.participant_identifier
                )
        else:
            ctx.sender.send_text(ctx.formatted_message, ctx.participant_identifier)

        # Send supported files — each file handles its own error
        for file in ctx.files_to_send:
            try:
                ctx.sender.send_file(file, ctx.participant_identifier)
            except Exception as e:
                ctx.processing_errors.append(f"Failed to send file: {e}")
                # Fallback to link
                link = file.download_link(ctx.experiment_session.id)
                ctx.sender.send_text(link, ctx.participant_identifier)
```

**Maps to**: `send_text_to_user()`, `send_voice_to_user()`, `send_file_to_user()` (various lines)

> **Review decisions applied**:
> - **Single sending stage**: `ResponseSendingStage` is the ONLY stage that sends messages. All other stages set `early_exit_response` and let this stage handle delivery.
> - **Always fires**: `should_run` returns `True` when there's either a `formatted_message` or `early_exit_response`. The early exit path sends the response text; the normal path sends the formatted bot response with files.
> - Error handling is per-stage (Decision 5).

#### Stage 11: ActivityTrackingStage

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

### 4\. Pipeline Orchestrator

```py
class MessageProcessingPipeline:
    """Orchestrates message processing through stages"""

    def __init__(self, stages: list[ProcessingStage]):
        self.stages = stages

    def process(self, ctx: MessageProcessingContext) -> MessageProcessingContext:
        """Run all stages in sequence.

        Each stage's __call__ checks should_run() internally (which checks
        early_exit_response). The pipeline itself does not catch errors —
        each stage handles its own errors (Decision 5).
        """
        for stage in self.stages:
            stage(ctx)  # __call__ checks should_run() → skips if early_exit_response is set

        return ctx
```

> **Review decision applied**: Pipeline no longer has a `break` on early exit. Instead, each stage's `should_run()` (which checks `early_exit_response is None`) handles skipping. This is simpler and means the pipeline is just a loop — stages own their control flow.

### 5\. Channel Implementation

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
        ConsentFlowStage is always included; its should_run() checks
        ctx.capabilities.supports_conversational_consent internally.
        ResponseSendingStage is the sole stage that sends messages.
        """
        return MessageProcessingPipeline([
            ParticipantValidationStage(),
            SessionResolutionStage(),
            MessageTypeValidationStage(),
            ConsentFlowStage(),
            QueryExtractionStage(),
            ChatMessageCreationStage(),
            BotInteractionStage(),
            ResponseFormattingStage(),
            EarlyExitResponseStage(),
            ResponseSendingStage(),
            ActivityTrackingStage(),
        ])

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

        # Process through pipeline
        with self.trace_service.trace(
            trace_name=self.experiment.name,
            session=self.experiment_session,
            inputs={"input": message.model_dump()},
        ) as span:
            ctx = self.pipeline.process(ctx)

            # Determine response
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
> - **ConsentFlowStage always included**: No conditional in `_build_pipeline`. The stage's `should_run()` handles the skip logic.
> - **11 stages**: ChatMessageCreationStage, EarlyExitResponseStage added. ResponseSendingStage is the sole message-sending stage (always fires).
> - **Dynamic `_get_capabilities()`** (Decision 3): Method can be overridden for channels where capabilities depend on runtime state (e.g., provider-dependent file support).

### 6\. Concrete Channel Example

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
    assert ctx.early_exit_response is None

def test_participant_validation_blocks_private_experiment():
    ctx = _make_context(
        experiment=Mock(is_public=False, is_participant_allowed=Mock(return_value=False)),
    )

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is False
    assert "not allowed" in ctx.early_exit_response
```

Fast, DB-free, no complex setup \- just test the stage logic with stubs\!

### 3\. **Explicit State** ✅

Context makes all state explicit and traceable:

- What was the input?
- What was computed at each stage?
- Why did processing stop early? (check `early_exit_response`)
- Stage performance visible via trace spans

### 4\. **Easy to Extend** ✅

Add new stages without modifying existing ones:

```py
class RateLimitingStage(ProcessingStage):
    """Check if user has exceeded rate limit"""

    def should_run(self, ctx) -> bool:
        return ctx.early_exit_response is None and ctx.participant_allowed

    def process(self, ctx) -> None:
        if self._is_rate_limited(ctx.participant_identifier):
            ctx.early_exit_response = "Too many messages. Please wait."
```

Just insert into pipeline \- no changes to other stages\!

### 5\. **Channel-Specific Customization** ✅

Channels can:

- Add stages: `pipeline.stages.insert(0, CustomStage())`
- Remove stages: `pipeline.stages = [s for s in stages if not isinstance(s, ConsentFlowStage)]`
- Replace stages: `pipeline.stages[2] = CustomValidationStage()`
- Reorder stages: `pipeline.stages.sort(key=...)`

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

## Challenges & Solutions

### Challenge 1: Channel-Specific Callbacks

**Problem**: Stages like QueryExtractionStage need channel-specific behavior:

- `transcription_started()` \- Telegram shows "uploading voice" indicator
- `echo_transcript()` \- Some channels echo back the transcript
- `submit_input_to_llm()` \- Telegram shows "typing" indicator

**Solution A: Callbacks Object**

```py
class ChannelCallbacks:
    """Channel-specific callback hooks"""
    def transcription_started(self) -> None:
        pass  # Default: no-op

    def echo_transcript(self, transcript: str) -> None:
        pass  # Default: no-op

    def submit_input_to_llm(self) -> None:
        pass  # Default: no-op

class TelegramCallbacks(ChannelCallbacks):
    def __init__(self, telegram_bot, experiment_session):
        self.bot = telegram_bot
        self.session = experiment_session

    def transcription_started(self):
        self.bot.send_chat_action(
            self.session.participant.identifier,
            "upload_voice"
        )

    def submit_input_to_llm(self):
        self.bot.send_chat_action(
            self.session.participant.identifier,
            "typing"
        )
```

Stages access callbacks via context:

```py
class QueryExtractionStage(ProcessingStage):
    # Zero-arg — no __init__ needed

    def process(self, ctx):
        ctx.callbacks.transcription_started()  # Channel-specific hook
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

**Decision**: Use **Solution A (Callbacks Object)** injected into the context. The `ChannelCallbacks` instance is set on `ctx.callbacks` by `ChannelBase.new_user_message()`. This keeps stages zero-arg while maintaining a clean, testable callbacks interface with no-op defaults.

### Challenge 2: Early Exit Handling

**Problem**: Some stages need to exit early (participant not allowed, unsupported message, consent flow).

**Decision**: Single `early_exit_response` field on context:

```py
ctx.early_exit_response = "Appropriate message"
```

When `early_exit_response` is set, `ProcessingStage.should_run()` returns `False` for all subsequent stages. No separate boolean flag needed — the presence of the response string is the signal. This eliminates the risk of the two fields getting out of sync.

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
        early_exit_response=None,
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
    assert ctx.early_exit_response is None

def test_blocks_participant_in_private_experiment():
    ctx = _make_context(
        experiment=Mock(is_public=False, is_participant_allowed=Mock(return_value=False)),
    )

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is False
    assert "not allowed" in ctx.early_exit_response
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

    pipeline = MessageProcessingPipeline([
        ParticipantValidationStage(),
        SessionResolutionStage(),
        # ... remaining stages
    ])
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
- **ConsentFlowStage**: Each status transition (SETUP→PENDING, PENDING→ACTIVE, PENDING→PENDING_PRE_SURVEY, PENDING_PRE_SURVEY→ACTIVE), consent denied, channel without consent support, experiment without consent enabled
- **QueryExtractionStage**: Text message, voice message, voice transcription failure
- **ChatMessageCreationStage**: Normal creation, session without chat
- **BotInteractionStage**: Successful response, bot error, response with files, response without files
- **ResponseFormattingStage**: Text response, voice response, citations, no citations, unsupported files
- **EarlyExitResponseStage**: Early exit with session (persists), early exit without session (no-op), no early exit (skips)
- **ResponseSendingStage**: Early exit response sent, normal text send, voice send, file send success, file send failure (fallback to link), no response at all (no-op)
- **ActivityTrackingStage**: Session update, versioned experiment tracking

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

    # Control flow — single field for early exit
    early_exit_response: str | None = None

    # Error tracking
    processing_errors: list[str] = field(default_factory=list)
```

**1.2 Create Channel Abstractions** (`apps/chat/channel_abstractions.py`):

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
    """

    def transcription_started(self) -> None:
        pass

    def transcription_finished(self, transcript: str) -> None:
        pass

    def echo_transcript(self, transcript: str) -> None:
        pass

    def submit_input_to_llm(self) -> None:
        pass

    def get_message_audio(self, message: 'BaseMessage') -> BytesIO:
        raise NotImplementedError("Channel must implement audio retrieval")


class ChannelSender(ABC):
    """Abstract base for sending messages to users."""

    @abstractmethod
    def send_text(self, text: str, recipient: str) -> None: ...

    @abstractmethod
    def send_voice(self, audio: 'SynthesizedAudio', recipient: str) -> None: ...

    @abstractmethod
    def send_file(self, file: 'File', recipient: str) -> None: ...
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

**Commit**: "Add MessageProcessingContext, ChannelCallbacks, ChannelCapabilities, ChannelSender infrastructure"

---

### Phase 2: Extract First Stage (Week 2-3)

**Goal**: Prove the pattern works with simplest stage

**2.1 Create Stage Base** (`apps/chat/channel_stages.py`):

```py
from abc import ABC, abstractmethod

class ProcessingStage(ABC):
    """Base class for stateless, zero-arg processing stages."""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        """Default: run if no early exit triggered."""
        return ctx.early_exit_response is None

    @abstractmethod
    def process(self, ctx: MessageProcessingContext) -> None:
        """Process context, modifying it in place"""
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
            ctx.early_exit_response = "Sorry, you are not allowed to chat to this bot"
```

**2.3 Use Stage in ChannelBase**:

```py
class ChannelBase(ABC):
    def _new_user_message_internal(self, ctx: MessageProcessingContext):
        # NEW: Use stage for participant validation
        ParticipantValidationStage()(ctx)  # __call__ handles should_run + tracing

        if ctx.early_exit_response:
            return ChatMessage(content=ctx.early_exit_response)

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
from unittest.mock import Mock
from apps.chat.channel_stages import ParticipantValidationStage

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
        early_exit_response=None,
        participant_allowed=False,
        participant_identifier=None,
    )
    defaults.update(overrides)
    return Mock(**defaults)

def test_participant_validation_allows_public_experiment():
    ctx = _make_context(experiment=Mock(is_public=True))

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is True
    assert ctx.early_exit_response is None

def test_participant_validation_blocks_private_experiment():
    ctx = _make_context(
        experiment=Mock(is_public=False, is_participant_allowed=Mock(return_value=False)),
    )

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is False
    assert "not allowed" in ctx.early_exit_response
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
        return ctx.early_exit_response is None and ctx.participant_allowed

    def process(self, ctx) -> None:
        # Handle /reset command
        if self._is_reset_command(ctx):
            self._handle_reset(ctx)
            return

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
            ctx.early_exit_response = self._generate_error_message(ctx)
```

**Tests**: Test supported/unsupported message types

**Commit**: "Extract MessageTypeValidationStage"

**3.3 ConsentFlowStage** (Week 4-5): Most complex stage \- consent state machine.

```py
class ConsentFlowStage(ProcessingStage):
    # Zero-arg — uses ctx.capabilities for consent support check

    def should_run(self, ctx) -> bool:
        if ctx.early_exit_response is not None:
            return False
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
        # Must handle: chat history, seed message, non-consent path, message sending
        # ...
```

**Tests**: Test each consent state transition, channel without consent, experiment without consent

**Commit**: "Extract ConsentFlowStage with state machine"

**3.4 QueryExtractionStage** (Week 5):

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

**3.5 ChatMessageCreationStage** (Week 5):

```py
class ChatMessageCreationStage(ProcessingStage):
    """Creates the ChatMessage DB record for the user's message."""

    def should_run(self, ctx) -> bool:
        return ctx.early_exit_response is None and ctx.user_query is not None

    def process(self, ctx) -> None:
        ctx.chat_message = ChatMessage.objects.create(
            chat=ctx.experiment_session.chat,
            message_type=ChatMessageType.HUMAN,
            content=ctx.user_query,
        )
```

**Tests**: Test message creation, session without chat

**Commit**: "Extract ChatMessageCreationStage"

**3.6 BotInteractionStage** (Week 6):

```py
class BotInteractionStage(ProcessingStage):
    # Zero-arg — uses ctx.callbacks for typing indicator

    def should_run(self, ctx) -> bool:
        return ctx.early_exit_response is None and ctx.user_query is not None

    def process(self, ctx) -> None:
        ctx.callbacks.submit_input_to_llm()

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

**3.7 ResponseFormattingStage** (Week 6):

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

**3.8 EarlyExitResponseStage** (Week 6):

```py
class EarlyExitResponseStage(ProcessingStage):
    """Persists early_exit_response to chat history.
    Uses _add_to_history (moved out of ConsentFlowStage).
    """

    def should_run(self, ctx) -> bool:
        return ctx.early_exit_response is not None

    def process(self, ctx) -> None:
        if ctx.experiment_session:
            self._add_to_history(ctx, ctx.early_exit_response)
```

**Tests**: Test persistence with early exit set, test no-op when no early exit

**Commit**: "Extract EarlyExitResponseStage"

**3.9 Update ResponseSendingStage** (Week 6):

Update `ResponseSendingStage` to always fire and handle both early exit and normal responses. This is the sole stage that sends messages to the user.

**Commit**: "Update ResponseSendingStage to handle early exit responses"

---

### Phase 4: Introduce Pipeline (Week 7\)

**Goal**: Wire stages together with pipeline orchestrator

**4.1 Create Pipeline** (`apps/chat/channel_pipeline.py`):

```py
class MessageProcessingPipeline:
    """Orchestrates message processing through stages"""

    def __init__(self, stages: list[ProcessingStage]):
        self.stages = stages

    def process(self, ctx: MessageProcessingContext) -> MessageProcessingContext:
        """Run all stages sequentially.
        Each stage's __call__ checks should_run() internally.
        No pipeline-level error handling — each stage handles its own errors.
        """
        for stage in self.stages:
            stage(ctx)

        return ctx
```

**4.2 Update ChannelBase**:

```py
class ChannelBase(ABC):
    def __init__(self, ...):
        # ... existing init
        self.pipeline = self._build_pipeline()

    def _build_pipeline(self) -> MessageProcessingPipeline:
        """Build default processing pipeline.
        All stages are zero-arg. ConsentFlowStage always included.
        ResponseSendingStage is the sole message-sending stage.
        """
        return MessageProcessingPipeline([
            ParticipantValidationStage(),
            SessionResolutionStage(),
            MessageTypeValidationStage(),
            ConsentFlowStage(),
            QueryExtractionStage(),
            ChatMessageCreationStage(),
            BotInteractionStage(),
            ResponseFormattingStage(),
            EarlyExitResponseStage(),
            ResponseSendingStage(),
            ActivityTrackingStage(),
        ])

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
    def __init__(self, telegram_bot, participant_id):
        self.bot = telegram_bot
        self.participant_id = participant_id

    def transcription_started(self):
        self.bot.send_chat_action(self.participant_id, "upload_voice")

    def submit_input_to_llm(self):
        self.bot.send_chat_action(self.participant_id, "typing")

class TelegramSender(ChannelSender):
    def __init__(self, telegram_bot):
        self.bot = telegram_bot

    def send_text(self, text, recipient):
        self.bot.send_message(recipient, text)

    # ... etc

class TelegramChannel(ChannelBase):
    def _get_callbacks(self) -> ChannelCallbacks:
        return TelegramCallbacks(self.telegram_bot, self.participant_identifier)

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
| 1 | Architecture | Single `early_exit_response` | Removed `should_process_message`. One field for control flow — its presence signals early exit. |
| 2 | Architecture | Callbacks/sender/capabilities on context | Stages are zero-arg. Channel-specific dependencies are injected into `ctx` by `ChannelBase.new_user_message()`. |
| 3 | Architecture | Runtime `_get_capabilities()` | Method on ChannelBase, overridable for provider-dependent channels (e.g., WhatsApp file support varies). |
| 4 | Architecture | Pre-set session on context | Web/Slack channels set `ctx.experiment_session` at creation. `SessionResolutionStage` respects this. |
| 5 | Code Quality | Each stage handles own errors | No pipeline-level try/catch. Stages append to `ctx.processing_errors` for observability. |
| 6 | Code Quality | Separate `ChatMessageCreationStage` | Stage between QueryExtraction and BotInteraction. DB record exists before bot call. |
| 7 | Code Quality | `/reset` inside `SessionResolutionStage` | Reset is session lifecycle — belongs in the stage that manages sessions. |
| 8 | Code Quality | ConsentFlowStage sub-behaviors explicit | Docstring documents: state transitions, seed message, non-consent path. Chat history and sending moved out. |
| — | Update | `EarlyExitResponseStage` | Persists `early_exit_response` to chat history. Uses `_add_to_history` (moved out of ConsentFlowStage). |
| — | Update | `ResponseSendingStage` always fires | Sole stage that sends messages. Handles both normal responses and early exit responses. No other stage sends messages. |
| 9 | Testing | DB-free stage unit tests | Use stubs/mocks (`unittest.mock.Mock`), not factories. No `@pytest.mark.django_db` for stage tests. |
| 10 | Testing | Phased test migration | Migrate tests alongside code — as stages are extracted, write new stage tests and update old ones. |
| 11 | Testing | Edge case test checklist | Per-stage checklist of edge cases to test (see Testing Strategy section). |
| 12 | Testing | Citation tests as pure unit tests | `_format_reference_section` tests use string inputs — no DB, no factories. |
| 13 | Performance | `select_related` on session queries | `SessionResolutionStage` uses `.select_related("experiment", "participant")`. |
| 14 | Performance | Document `select_related` for experiment FKs | Experiment queries throughout stages should use appropriate `select_related`. |
| — | Example feedback | Trace spans in `__call__` | `ProcessingStage.__call__` uses `ctx.trace_service.span()`, not `time.monotonic()` timing. |
| — | Example feedback | `supports_conversational_consent` on `ChannelCapabilities` | Not a ClassVar on ChannelBase. ConsentFlowStage checks `ctx.capabilities.supports_conversational_consent`. |

### Performance Notes (Deferred)

These were noted during review but deferred for later implementation:

- **`count()` → `exists()`**: Where the code checks if any sessions exist, use `.exists()` instead of `.count() > 0`.
- **Bot instance reuse**: Consider caching bot instances across messages within the same session to avoid repeated initialization.
