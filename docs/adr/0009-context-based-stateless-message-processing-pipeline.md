# ADR-0009: Context-based stateless message processing pipeline

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

## Context

The legacy `ChannelBase` in `apps/chat/channels.py` was a stateful monolith. State accumulated across a single `new_user_message()` call, and that method was a long chain of interleaved side effects with per-platform branching. Adding a channel meant subclassing and overriding methods coupled to shared state, which made behaviours hard to test in isolation and easy to break across unrelated channels.

We evaluated three alternatives — mixin inheritance, a registry-and-adapter composition pattern, and incremental adapter extraction — but each either preserved the stateful core or traded one form of complexity for another.

## Decision

We will replace the stateful `ChannelBase` with a pipeline architecture:

- A single `MessageProcessingContext` dataclass carries all state for one message interaction.
- It is passed sequentially through stateless `ProcessingStage` instances orchestrated by `MessageProcessingPipeline`.
- The context is mutable (stages write results directly to it); this can be revisited if immutability becomes valuable.
- Channels become thin builders: `ChannelBase._build_pipeline()` assembles the stage list, and channel-specific dependencies (callbacks, sender, capabilities) are injected into the context at creation time. Every stage is therefore a zero-argument constructor with no per-instance state.

### Example

```python
# 1. Context carries all state for one message interaction.
ctx = MessageProcessingContext(
    message=inbound_message,
    experiment=experiment,
    experiment_channel=channel,
    callbacks=TelegramCallbacks(bot),   # channel-specific, injected once
    sender=TelegramSender(bot),
    capabilities=ChannelCapabilities(supports_voice_replies=True, ...),
    trace_service=trace_service,
)

# 2. Each stage is a zero-arg class that reads from and writes to ctx.
class QueryExtractionStage(ProcessingStage):
    def process(self, ctx: MessageProcessingContext) -> None:
        if ctx.message.content_type == MESSAGE_TYPES.VOICE:
            ctx.user_query = self._transcribe_voice(ctx)
        else:
            ctx.user_query = ctx.message.message_text

# 3. The pipeline runs core stages in order, then always runs terminal stages.
pipeline = MessageProcessingPipeline(
    core_stages=[SessionResolutionStage(), QueryExtractionStage(), BotInteractionStage(), ...],
    terminal_stages=[ResponseSendingStage(), PersistenceStage(), ...],
)
pipeline.process(ctx)  # ctx.bot_response is set by the time this returns
```

## Consequences

- Each stage is independently testable with mocks — no database or factories required.
- Adding new stages or checks requires no changes to existing stages.
- All processing state is explicit in `MessageProcessingContext` rather than scattered across instance variables.
- Each stage executes inside a trace span via `ProcessingStage.__call__`, integrating with existing observability.
- The pipeline pattern is less familiar than class-based views; new contributors must learn the stage contract.
- Migrating all platform channels requires one PR per channel, so old and new implementations coexist temporarily.

## Alternatives considered

- **Mixin-based inheritance** → introduces complex MRO and base/mixin coupling; mixins hard to test in isolation. Rejected.
- **Registry + adapters composition** → layers Adapters, Strategies, and Registry on each other, trading one complexity for another. Rejected.
- **Incremental adapter extraction** → low-risk but leaves the stateful `ChannelBase` core unchanged, not addressing the testability problem. Rejected.
- **Immutable context (stages return new context)** → cleaner functional model but more verbose; the mutable model is sufficient for now. Deferred.
