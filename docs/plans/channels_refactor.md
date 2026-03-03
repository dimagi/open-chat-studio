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
    """Immutable context passed through processing stages"""

    # Input (immutable)
    message: BaseMessage
    experiment: Experiment
    experiment_channel: ExperimentChannel

    # Services (injected, immutable)
    messaging_adapter: MessagingAdapter
    trace_service: TracingService

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

    # Control flow flags
    should_skip_consent: bool = False
    should_process_message: bool = True  # Set to False for early exit
    early_exit_response: str | None = None  # Response for early exit

    # Metadata
    processing_errors: list[str] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)
```

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
    """Base class for stateless processing stages"""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        """Determine if this stage should run based on context state. You only need to override this if you need to check additional logic """
        return ctx.should_process_message

    @abstractmethod
    def process(self, ctx: MessageProcessingContext) -> None:
        """Process the context, modifying it in place"""
        pass

    def __call__(self, ctx: MessageProcessingContext) -> None:
        """Execute stage if conditions are met"""
        if self.should_run(ctx):
            with ctx.trace_service.span(self.__class__.__name__, ctx.context_for_trace()) as span:
                self.process(ctx)
                span.set_outputs(ctx.context_for_trace())
```

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
            ctx.should_process_message = False
            ctx.early_exit_response = "Sorry, you are not allowed to chat to this bot"
```

**Maps to**: `_participant_is_allowed()` (line 378-381)

#### Stage 2: SessionResolutionStage

```py
class SessionResolutionStage(ProcessingStage):
    """Loads or creates experiment session"""

    def process(self, ctx: MessageProcessingContext) -> None:
        # Check for existing session
        existing_session = ExperimentSession.objects.filter(
            experiment=ctx.experiment.get_working_version(),
            participant__identifier=ctx.participant_identifier,
        ).exclude(status__in=STATUSES_FOR_COMPLETE_CHATS).first()

        if existing_session:
            ctx.experiment_session = existing_session
        else:
            # Create new session
            ctx.experiment_session = self._create_session(ctx)
```

**Maps to**: `_ensure_sessions_exists()` (line 697-726)

#### Stage 3: MessageTypeValidationStage

```py
class MessageTypeValidationStage(ProcessingStage):
    """Validates message type is supported by channel"""

    def __init__(self, supported_message_types: list[MESSAGE_TYPES]):
        self.supported_message_types = supported_message_types

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.message.content_type not in self.supported_message_types:
            ctx.should_process_message = False
            ctx.early_exit_response = self._generate_unsupported_message_response(ctx)
```

**Maps to**: `is_message_type_supported()` and `_handle_unsupported_message()` (lines 789-804)

#### Stage 4: ConsentFlowStage

```py
class ConsentFlowStage(ProcessingStage):
    """Handles conversational consent state machine"""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        # Only run if channel supports consent flow and experiment has it enabled
        if not super().should_run(ctx):
            return False

        supports_consent = getattr(
            ctx, 'supports_conversational_consent_flow', True
        )
        return (
            supports_consent and
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
            ctx.should_process_message = False
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

            ctx.should_process_message = False
            ctx.early_exit_response = response

        elif session.status == SessionStatus.PENDING_PRE_SURVEY:
            if self._user_gave_consent(ctx):
                response = self._start_conversation(ctx)
                session.update_status(SessionStatus.ACTIVE)
            else:
                response = self._ask_for_survey(ctx)

            ctx.should_process_message = False
            ctx.early_exit_response = response
```

**Maps to**: `_handle_pre_conversation_requirements()` (lines 409-441)

**Key Feature**: This stage's `should_run()` implements the complex conditional logic for when consent flow applies.

#### Stage 5: QueryExtractionStage

```py
class QueryExtractionStage(ProcessingStage):
    """Extracts user query from message (handles voice transcription)"""

    def __init__(self, channel_callbacks: 'ChannelCallbacks'):
        self.callbacks = channel_callbacks

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.message.content_type == MESSAGE_TYPES.VOICE:
            ctx.user_query = self._transcribe_voice(ctx)
        else:
            ctx.user_query = ctx.message.message_text

    def _transcribe_voice(self, ctx: MessageProcessingContext) -> str:
        # Callback to channel-specific transcription started hook
        self.callbacks.transcription_started()

        audio_file = self.callbacks.get_message_audio(ctx.message)
        transcript = self._transcribe_audio(ctx, audio_file)

        if ctx.experiment.echo_transcript:
            self.callbacks.echo_transcript(transcript)

        self.callbacks.transcription_finished(transcript)
        return transcript
```

**Maps to**: `_extract_user_query()` and `_get_voice_transcript()` (lines 496-499, 664-673)

**Challenge**: This stage needs channel-specific callbacks (echo\_transcript, transcription\_started, etc.)

#### Stage 6: BotInteractionStage

```py
class BotInteractionStage(ProcessingStage):
    """Gets response from bot"""

    def __init__(self, channel_callbacks: 'ChannelCallbacks'):
        self.callbacks = channel_callbacks

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return super().should_run(ctx) and ctx.user_query is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        # Callback for "typing" indicator or similar
        self.callbacks.submit_input_to_llm()

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

#### Stage 7: ResponseFormattingStage

```py
class ResponseFormattingStage(ProcessingStage):
    """Formats bot response for channel (handles citations, voice, etc.)"""

    def __init__(self, channel_capabilities: 'ChannelCapabilities'):
        self.capabilities = channel_capabilities

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
                message, files, self.capabilities
            )
            message = self._append_attachment_links(message, uncited_files)
            ctx.formatted_message = message

        # Separate files into supported/unsupported for channel
        if self.capabilities.supports_files:
            ctx.files_to_send, ctx.unsupported_files = (
                self._split_by_support(files, self.capabilities)
            )
```

**Maps to**: `send_message_to_user()` and `_format_reference_section()` (lines 501-546, 548-616)

#### Stage 8: ResponseSendingStage

```py
class ResponseSendingStage(ProcessingStage):
    """Sends formatted response to user via channel"""

    def __init__(self, channel_sender: 'ChannelSender'):
        self.sender = channel_sender

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.formatted_message is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.voice_audio:
            self.sender.send_voice(ctx.voice_audio, ctx.participant_identifier)
            if ctx.additional_text_message:
                self.sender.send_text(
                    ctx.additional_text_message,
                    ctx.participant_identifier
                )
        else:
            self.sender.send_text(ctx.formatted_message, ctx.participant_identifier)

        # Send supported files
        for file in ctx.files_to_send:
            try:
                self.sender.send_file(file, ctx.participant_identifier)
            except Exception as e:
                # Fallback to link
                link = file.download_link(ctx.experiment_session.id)
                self.sender.send_text(link, ctx.participant_identifier)
```

**Maps to**: `send_text_to_user()`, `send_voice_to_user()`, `send_file_to_user()` (various lines)

#### Stage 9: ActivityTrackingStage

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
        """Run all stages in sequence"""
        for stage in self.stages:
            if not ctx.should_process_message and ctx.early_exit_response:
                # Early exit - skip remaining stages
                break

            stage(ctx)  # Calls stage.should_run() and stage.process()

        return ctx
```

### 5\. Channel Implementation

Channels become **pipeline builders** \+ **callback providers**:

```py
class ChannelBase(ABC):
    """Base channel with pipeline architecture"""

    # Class-level configuration (same as before)
    voice_replies_supported: ClassVar[bool] = False
    supported_message_types: ClassVar[list] = []
    supports_conversational_consent_flow: ClassVar[bool] = True

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
        """Build standard processing pipeline (subclasses can override)"""
        return MessageProcessingPipeline([
            ParticipantValidationStage(),
            SessionResolutionStage(),
            MessageTypeValidationStage(self.supported_message_types),
            ConsentFlowStage() if self.supports_conversational_consent_flow else None,
            QueryExtractionStage(self._get_callbacks()),
            BotInteractionStage(self._get_callbacks()),
            ResponseFormattingStage(self._get_capabilities()),
            ResponseSendingStage(self._get_sender()),
            ActivityTrackingStage(),
        ])

    def new_user_message(self, message: BaseMessage) -> ChatMessage:
        """Main entry point - runs message through pipeline"""

        # Create context
        ctx = MessageProcessingContext(
            message=message,
            experiment=self.experiment,
            experiment_channel=self.experiment_channel,
            experiment_session=self.experiment_session,
            messaging_adapter=self.messaging_adapter,
            trace_service=self.trace_service,
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
        """Get channel-specific callbacks for stages"""
        pass

    @abstractmethod
    def _get_sender(self) -> 'ChannelSender':
        """Get channel-specific sender"""
        pass

    def _get_capabilities(self) -> 'ChannelCapabilities':
        """Get channel capabilities (default implementation)"""
        return ChannelCapabilities(
            supports_voice=self.voice_replies_supported,
            supported_message_types=self.supported_message_types,
            supports_files=self.supports_multimedia,
        )
```

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

    # Can override pipeline for channel-specific flow
    def _build_pipeline(self) -> MessageProcessingPipeline:
        # Add Telegram-specific stage for checking consent
        base_pipeline = super()._build_pipeline()
        base_pipeline.stages.insert(0, TelegramConsentCheckStage())
        return base_pipeline
```

**Much simpler\!** Channel-specific behavior is isolated to callbacks and sender, not spread throughout the base class.

## Advantages of Context-Based Architecture

### 1\. **Separation of Concerns** ✅

Each stage has one responsibility:

- ParticipantValidation: Only checks participant
- SessionResolution: Only deals with sessions
- ConsentFlow: Only handles consent state machine

### 2\. **Testability** ✅✅✅

**Huge win** \- can test each stage independently:

```py
def test_participant_validation_allows_public_experiment():
    ctx = MessageProcessingContext(
        experiment=public_experiment,
        message=BaseMessage(participant_id="anyone"),
        ...
    )

    stage = ParticipantValidationStage()
    stage.process(ctx)

    assert ctx.participant_allowed == True
    assert ctx.should_process_message == True

def test_participant_validation_blocks_private_experiment():
    ctx = MessageProcessingContext(
        experiment=private_experiment,
        message=BaseMessage(participant_id="blocked_user"),
        ...
    )

    stage = ParticipantValidationStage()
    stage.process(ctx)

    assert ctx.participant_allowed == False
    assert ctx.should_process_message == False
    assert "not allowed" in ctx.early_exit_response
```

No mocking, no complex setup \- just test the stage logic\!

### 3\. **Explicit State** ✅

Context makes all state explicit and traceable:

- What was the input?
- What was computed at each stage?
- Why did processing stop early?
- How long did each stage take?

### 4\. **Easy to Extend** ✅

Add new stages without modifying existing ones:

```py
class RateLimitingStage(ProcessingStage):
    """Check if user has exceeded rate limit"""

    def should_run(self, ctx) -> bool:
        return ctx.participant_allowed

    def process(self, ctx) -> None:
        if self._is_rate_limited(ctx.participant_identifier):
            ctx.should_process_message = False
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

Context can track timing and metadata:

```py
@contextmanager
def time_stage(self, stage_name: str):
    start = time.time()
    try:
        yield
    finally:
        self.stage_timings[stage_name] = time.time() - start
```

Easy to log, trace, and debug\!

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

Stages receive callbacks:

```py
class QueryExtractionStage(ProcessingStage):
    def __init__(self, callbacks: ChannelCallbacks):
        self.callbacks = callbacks

    def process(self, ctx):
        self.callbacks.transcription_started()  # Channel-specific hook
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

**Recommendation**: Use **Solution A (Callbacks Object)** \- cleaner and more testable.

### Challenge 2: Early Exit Handling

**Problem**: Some stages need to exit early (participant not allowed, unsupported message, consent flow).

**Solution**: Context flags (already shown above):

```py
ctx.should_process_message = False
ctx.early_exit_response = "Appropriate message"
```

Pipeline checks these flags and skips remaining stages.

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

### Unit Tests for Stages

```py
# tests/channels/stages/test_participant_validation.py
def test_allows_participant_in_public_experiment():
    ctx = MessageProcessingContext(
        experiment=ExperimentFactory(is_public=True),
        message=BaseMessage(participant_id="anyone"),
    )

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed
    assert ctx.should_process_message

def test_blocks_participant_in_private_experiment():
    experiment = ExperimentFactory(is_public=False)
    ctx = MessageProcessingContext(
        experiment=experiment,
        message=BaseMessage(participant_id="blocked"),
    )

    ParticipantValidationStage().process(ctx)

    assert not ctx.participant_allowed
    assert not ctx.should_process_message
    assert "not allowed" in ctx.early_exit_response
```

**No mocking needed\!** Just pure logic testing.

### Integration Tests for Pipeline

```py
def test_full_pipeline_happy_path(mock_bot):
    ctx = MessageProcessingContext(
        experiment=active_experiment,
        experiment_channel=telegram_channel,
        message=BaseMessage(
            participant_id="allowed_user",
            message_text="Hello"
        ),
        messaging_adapter=MockAdapter(),
    )

    pipeline = TelegramChannel._build_pipeline()
    result_ctx = pipeline.process(ctx)

    assert result_ctx.participant_allowed
    assert result_ctx.experiment_session is not None
    assert result_ctx.bot_response is not None
    assert result_ctx.formatted_message is not None
```

### Integration Tests for Channels (End-to-End)

```py
def test_telegram_channel_processes_message(experiment, telegram_channel):
    channel = TelegramChannel(experiment, telegram_channel)
    message = TelegramMessage.parse(telegram_update)

    response = channel.new_user_message(message)

    assert response.content
    # Check side effects (session created, messages sent, etc.)
```

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

    # Control flow
    should_process_message: bool = True
    early_exit_response: str | None = None

    # Metadata
    stage_timings: dict[str, float] = field(default_factory=dict)
```

**1.2 Create Callbacks Interface** (`apps/chat/channel_callbacks.py`):

```py
class ChannelCallbacks:
    """Base class for channel-specific callback hooks"""

    def transcription_started(self) -> None:
        """Called when voice transcription starts"""
        pass

    def transcription_finished(self, transcript: str) -> None:
        """Called when voice transcription finishes"""
        pass

    def echo_transcript(self, transcript: str) -> None:
        """Called to echo transcript back to user"""
        pass

    def submit_input_to_llm(self) -> None:
        """Called before sending input to LLM (e.g., show 'typing')"""
        pass

    def get_message_audio(self, message: 'BaseMessage') -> BytesIO:
        """Get audio content from message"""
        raise NotImplementedError("Channel must implement audio retrieval")
```

**1.3 Update ChannelBase** (still in `apps/chat/channels.py`):

```py
class ChannelBase(ABC):
    def __init__(self, experiment, experiment_channel, experiment_session=None,
                 messaging_adapter=None, trace_service=None):
        # ... existing init code

    def new_user_message(self, message: BaseMessage) -> ChatMessage:
        """Main entry point - gradually migrating to context-based"""
        # Create context
        ctx = MessageProcessingContext(
            message=message,
            experiment=self.experiment,
            experiment_channel=self.experiment_channel,
            experiment_session=self.experiment_session,
            messaging_adapter=self.messaging_adapter,
            trace_service=self.trace_service,
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

**Commit**: "Add MessageProcessingContext and ChannelCallbacks infrastructure"

---

### Phase 2: Extract First Stage (Week 2-3)

**Goal**: Prove the pattern works with simplest stage

**2.1 Create Stage Base** (`apps/chat/channel_stages.py`):

```py
from abc import ABC, abstractmethod

class ProcessingStage(ABC):
    """Base class for stateless processing stages"""

    @abstractmethod
    def should_run(self, ctx: MessageProcessingContext) -> bool:
        """Check if stage should run based on context"""
        pass

    @abstractmethod
    def process(self, ctx: MessageProcessingContext) -> None:
        """Process context, modifying it in place"""
        pass

    def __call__(self, ctx: MessageProcessingContext) -> None:
        """Execute stage if should_run returns True"""
        if self.should_run(ctx):
            import time
            start = time.time()
            try:
                self.process(ctx)
            finally:
                ctx.stage_timings[self.__class__.__name__] = time.time() - start
```

**2.2 Extract ParticipantValidationStage**:

```py
class ParticipantValidationStage(ProcessingStage):
    """Validates participant is allowed to interact"""

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return True  # Always run first

    def process(self, ctx: MessageProcessingContext) -> None:
        ctx.participant_identifier = ctx.message.participant_id

        if ctx.experiment.is_public:
            ctx.participant_allowed = True
            return

        ctx.participant_allowed = ctx.experiment.is_participant_allowed(
            ctx.participant_identifier
        )

        if not ctx.participant_allowed:
            ctx.should_process_message = False
            ctx.early_exit_response = "Sorry, you are not allowed to chat to this bot"
```

**2.3 Use Stage in ChannelBase**:

```py
class ChannelBase(ABC):
    def _new_user_message_internal(self, ctx: MessageProcessingContext):
        # NEW: Use stage for participant validation
        ParticipantValidationStage().process(ctx)

        if not ctx.should_process_message:
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
import pytest
from apps.chat.channel_context import MessageProcessingContext
from apps.chat.channel_stages import ParticipantValidationStage
from apps.utils.factories import ExperimentFactory
from apps.channels.datamodels import BaseMessage

def test_participant_validation_allows_public_experiment():
    ctx = MessageProcessingContext(
        message=BaseMessage(participant_id="anyone", message_text="hi"),
        experiment=ExperimentFactory(is_public=True),
        experiment_channel=None,
    )

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is True
    assert ctx.should_process_message is True
    assert ctx.early_exit_response is None

def test_participant_validation_blocks_private_experiment():
    experiment = ExperimentFactory(is_public=False)
    ctx = MessageProcessingContext(
        message=BaseMessage(participant_id="blocked_user", message_text="hi"),
        experiment=experiment,
        experiment_channel=None,
    )

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is False
    assert ctx.should_process_message is False
    assert "not allowed" in ctx.early_exit_response

def test_participant_validation_allows_whitelisted_user():
    experiment = ExperimentFactory(is_public=False)
    # Add participant to experiment's allowlist
    participant = Participant.objects.create(
        team=experiment.team,
        identifier="allowed@example.com"
    )
    experiment.participants.add(participant)

    ctx = MessageProcessingContext(
        message=BaseMessage(participant_id="allowed@example.com", message_text="hi"),
        experiment=experiment,
        experiment_channel=None,
    )

    ParticipantValidationStage().process(ctx)

    assert ctx.participant_allowed is True
```

**Validation**:

- New tests pass
- All existing tests still pass
- Participant validation logic now in stage

**Commit**: "Extract ParticipantValidationStage with tests"

---

### Phase 3: Extract Core Stages (Week 3-6)

**Goal**: Extract remaining stages one at a time

**3.1 SessionResolutionStage** (Week 3):

```py
class SessionResolutionStage(ProcessingStage):
    def should_run(self, ctx) -> bool:
        return ctx.participant_allowed and ctx.should_process_message

    def process(self, ctx) -> None:
        # Logic from _ensure_sessions_exists()
        if ctx.experiment_session:
            return  # Already have session

        # Try to load existing
        ctx.experiment_session = self._load_latest_session(ctx)

        # Create new if needed
        if not ctx.experiment_session:
            ctx.experiment_session = self._create_new_session(ctx)
```

**Tests**: Test session loading, creation, reset logic independently

**Commit**: "Extract SessionResolutionStage"

**3.2 MessageTypeValidationStage** (Week 4):

```py
class MessageTypeValidationStage(ProcessingStage):
    def __init__(self, supported_types: list[MESSAGE_TYPES]):
        self.supported_types = supported_types

    def should_run(self, ctx) -> bool:
        return ctx.should_process_message

    def process(self, ctx) -> None:
        if ctx.message.content_type not in self.supported_types:
            ctx.should_process_message = False
            ctx.early_exit_response = self._generate_error_message(ctx)
```

**Tests**: Test supported/unsupported message types

**Commit**: "Extract MessageTypeValidationStage"

**3.3 ConsentFlowStage** (Week 4-5): Most complex stage \- consent state machine.

```py
class ConsentFlowStage(ProcessingStage):
    def __init__(self, callbacks: ChannelCallbacks):
        self.callbacks = callbacks

    def should_run(self, ctx) -> bool:
        if not ctx.should_process_message:
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
        # ...
```

**Tests**: Test each consent state transition independently

**Commit**: "Extract ConsentFlowStage with state machine"

**3.4 QueryExtractionStage** (Week 5):

```py
class QueryExtractionStage(ProcessingStage):
    def __init__(self, callbacks: ChannelCallbacks):
        self.callbacks = callbacks

    def should_run(self, ctx) -> bool:
        return ctx.should_process_message

    def process(self, ctx) -> None:
        if ctx.message.content_type == MESSAGE_TYPES.VOICE:
            ctx.user_query = self._transcribe_voice(ctx)
        else:
            ctx.user_query = ctx.message.message_text
```

**Tests**: Test text extraction and voice transcription

**Commit**: "Extract QueryExtractionStage"

**3.5 BotInteractionStage** (Week 6):

```py
class BotInteractionStage(ProcessingStage):
    def should_run(self, ctx) -> bool:
        return ctx.user_query is not None

    def process(self, ctx) -> None:
        # Get bot instance
        if not ctx.bot:
            ctx.bot = get_bot(
                ctx.experiment_session,
                ctx.experiment,
                ctx.trace_service
            )

        # Process input
        ctx.bot_response = ctx.bot.process_input(
            ctx.user_query,
            attachments=ctx.message.attachments
        )

        ctx.files_to_send = ctx.bot_response.get_attached_files() or []
```

**Tests**: Test bot interaction with mocked bot

**Commit**: "Extract BotInteractionStage"

**3.6 ResponseFormattingStage** (Week 6):

```py
class ResponseFormattingStage(ProcessingStage):
    def __init__(self, capabilities, callbacks):
        self.capabilities = capabilities
        self.callbacks = callbacks

    def should_run(self, ctx) -> bool:
        return ctx.bot_response is not None

    def process(self, ctx) -> None:
        # Logic from send_message_to_user and _format_reference_section
        # Determines voice vs text
        # Formats citations
        # Splits supported/unsupported files
```

**Tests**: Test message formatting, citation handling, voice/text selection

**Commit**: "Extract ResponseFormattingStage"

---

### Phase 4: Introduce Pipeline (Week 7\)

**Goal**: Wire stages together with pipeline orchestrator

**4.1 Create Pipeline** (`apps/chat/channel_pipeline.py`):

```py
class MessageProcessingPipeline:
    """Orchestrates message processing through stages"""

    def __init__(self, stages: list[ProcessingStage]):
        self.stages = [s for s in stages if s is not None]  # Filter out None

    def process(self, ctx: MessageProcessingContext) -> MessageProcessingContext:
        """Run all stages sequentially"""
        for stage in self.stages:
            # Check for early exit
            if not ctx.should_process_message and ctx.early_exit_response:
                break

            stage(ctx)  # __call__ handles should_run check

        return ctx
```

**4.2 Update ChannelBase**:

```py
class ChannelBase(ABC):
    def __init__(self, ...):
        # ... existing init
        self.pipeline = self._build_pipeline()

    def _build_pipeline(self) -> MessageProcessingPipeline:
        """Build default processing pipeline"""
        return MessageProcessingPipeline([
            ParticipantValidationStage(),
            SessionResolutionStage(),
            MessageTypeValidationStage(self.supported_message_types),
            ConsentFlowStage(self._get_callbacks()) if self.supports_conversational_consent_flow else None,
            QueryExtractionStage(self._get_callbacks()),
            BotInteractionStage(),
            ResponseFormattingStage(self._get_capabilities(), self._get_callbacks()),
            ResponseSendingStage(self._get_sender()),
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
        )

        # Run pipeline
        with self.trace_service.trace(...) as span:
            ctx = self.pipeline.process(ctx)

            # Extract response
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
```

**4.3 Implement Callbacks for Each Channel**:

```py
class TelegramCallbacks(ChannelCallbacks):
    def __init__(self, telegram_bot, participant_id):
        self.bot = telegram_bot
        self.participant_id = participant_id

    def transcription_started(self):
        self.bot.send_chat_action(self.participant_id, "upload_voice")

    def submit_input_to_llm(self):
        self.bot.send_chat_action(self.participant_id, "typing")

    # ... etc

class TelegramChannel(ChannelBase):
    def _get_callbacks(self) -> ChannelCallbacks:
        return TelegramCallbacks(self.telegram_bot, self.participant_identifier)
```

**Validation**:

- All tests pass
- Message processing now flows through pipeline
- Can trace which stages run and how long they take

**Commit**: "Introduce MessageProcessingPipeline"
