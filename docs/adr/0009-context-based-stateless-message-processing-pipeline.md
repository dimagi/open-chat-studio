# ADR-0009: Context-based stateless message processing pipeline

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

## Context

The legacy `ChannelBase` in `apps/chat/channels.py` was a stateful monolith. Instance variables
(`self.experiment`, `self.experiment_session`, `self.message`, and a set of `@cached_property`
attributes) accumulated across a single `new_user_message()` call, and the resulting method was
a long sequence of interleaved side effects with conditional branching for each supported platform.
Adding a new channel meant subclassing and overriding methods that were tightly coupled to this
shared state, making it difficult to test individual behaviours in isolation and easy to break
unrelated channels when fixing one.

Three alternative refactoring strategies were evaluated: (1) mixin-based inheritance to break the
god-object into composable behaviours; (2) a registry-and-adapter composition pattern with
explicit capability discovery; and (3) incremental extraction of adapters around the existing
base without changing the core structure. All three either preserved the stateful core or traded
one form of complexity for another.

## Decision

We will replace the stateful `ChannelBase` with a pipeline architecture: a single
`MessageProcessingContext` dataclass carries all state for one message interaction, passed
sequentially through stateless `ProcessingStage` instances orchestrated by
`MessageProcessingPipeline`. The context is mutable — stages write results directly to it —
for simplicity; this can be revisited if immutability becomes valuable. Channels become thin
builders: `ChannelBase._build_pipeline()` assembles the stage list, and channel-specific
dependencies (callbacks, sender, capabilities) are injected into the context at creation time so
every stage remains a zero-argument constructor with no per-instance state.

### Example

A minimal illustration of the three moving parts — context, stage, pipeline — as they appear in
the codebase:

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

- Each stage is independently testable with mocks — no database or factories required for
  unit tests.
- Adding pipeline behaviours (new stages, new checks) requires no changes to existing stages.
- All processing state is explicit and traceable in `MessageProcessingContext` rather than
  scattered across instance variables that persist between method calls.
- Each stage automatically executes inside a trace span via `ProcessingStage.__call__`,
  integrating with existing observability infrastructure.
- The pipeline pattern is less familiar to Django contributors than class-based view patterns;
  new contributors need to learn the stage contract.
- Migrating all platform channels to the new architecture requires one PR per channel; the
  codebase temporarily contains both old and new implementations in parallel.

## Alternatives considered

- **Mixin-based inheritance**: Breaks the god-object into composable pieces but introduces
  complex MRO and tight coupling between base class and mixins; hard to test mixins in isolation.
  Rejected.
- **Registry + adapters composition**: Better separation via explicit adapters, but layers
  Adapters + Strategies + Registry on top of each other, trading one form of complexity for
  another. Rejected for simplicity.
- **Incremental adapter extraction**: Low-risk phased approach that preserves backward
  compatibility, but leaves the stateful `ChannelBase` core unchanged and doesn't address the
  root testability problem. Rejected.
- **Immutable context (stages return new context)**: Cleaner functional model but more
  verbose; the mutable model is sufficient and can be refined later. Deferred.
