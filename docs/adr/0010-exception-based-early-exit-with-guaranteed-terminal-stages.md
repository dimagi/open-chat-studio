# ADR-0010: Exception-based early exit with guaranteed terminal stages

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

<p class="adr-meta">Extends: <a href="0009-context-based-stateless-message-processing-pipeline.md">ADR-0009</a></p>

## Context

With the pipeline architecture introduced in ADR-0009, stages need a way to halt processing
early — for example, when a participant is not allowed to interact, a message type is
unsupported, or the consent flow requires a prompt before continuing. At the same time, a
subset of stages must always run regardless of whether processing succeeded or was interrupted:
response delivery, message persistence, and session activity tracking form an audit trail and
must fire even when something goes wrong.

Two control-flow options were considered: a flag on the context (`ctx.should_continue`) that
every stage checks before running, and an exception-based mechanism that removes the check from
stages entirely.

## Decision

We will use `EarlyExitResponse` as the mechanism for intentional short-circuits: any core stage
raises it to halt the remaining core stages and deliver a user-facing message. The pipeline
catches it, stores the message on `ctx.early_exit_response`, and continues to terminal stages
as normal. Unexpected exceptions are caught by a catch-all that generates a user-facing error
message via `EventBot` (preserving `ChatException` specificity), sets `ctx.early_exit_response`,
runs terminal stages, then re-raises so the caller knows processing failed. `GenerationCancelled`
and similar exceptions propagate immediately without triggering error-message generation or
terminal stages.

Stages must never read or set `ctx.early_exit_response` — the pipeline is the sole owner of
control flow. `ProcessingStage.should_run()` is for stage-specific preconditions only, not for
early-exit checking.

## Consequences

- No stage needs to check `ctx.early_exit_response`; forgetting to check is no longer an
  error class.
- Terminal stages are guaranteed to run on happy-path and `EarlyExitResponse` flows, ensuring
  responses are delivered and persisted even when a core stage short-circuits.
- Using exceptions for control flow is unconventional; developers may be surprised that
  `EarlyExitResponse` is a normal part of the happy path rather than an error.

## Alternatives considered

- **Flag-based control (`ctx.should_continue`)**: Each stage checks the flag before running.
  Rejected because forgetting to check produces silent wrong behaviour with no runtime signal.
