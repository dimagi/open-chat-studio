# ADR-0010: Exception-based early exit with guaranteed terminal stages

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

<p class="adr-meta">Extends: <a href="0009-context-based-stateless-message-processing-pipeline.md">ADR-0009</a></p>

## Context

In the pipeline architecture from ADR-0009, stages need a way to halt processing early (e.g. a disallowed participant, an unsupported message type, or a consent prompt). Independently, a subset of stages must always run — response delivery, message persistence, and session activity tracking form an audit trail that must fire even when processing is interrupted.

We considered a context flag (`ctx.should_continue`) that every stage checks, versus an exception-based mechanism that removes the check from stages.

## Decision

We will use exceptions to drive early exit and control flow:

- Any core stage raises `EarlyExitResponse` to halt remaining core stages and deliver a user-facing message. The pipeline catches it, stores the message on `ctx.early_exit_response`, and continues to terminal stages.
- Unexpected exceptions are caught by a catch-all that generates a user-facing error message via `EventBot` (preserving `ChatException` specificity), sets `ctx.early_exit_response`, runs terminal stages, then re-raises so the caller knows processing failed.
- `GenerationCancelled` and similar exceptions propagate immediately, skipping both error-message generation and terminal stages.
- Stages must never read or set `ctx.early_exit_response`; the pipeline is the sole owner of control flow. `ProcessingStage.should_run()` is for stage-specific preconditions only, not early-exit checking.

## Consequences

- No stage checks `ctx.early_exit_response` → forgetting to check is no longer an error class.
- Terminal stages run on happy-path and `EarlyExitResponse` flows → responses are delivered and persisted even when a core stage short-circuits.
- Exceptions for control flow are unconventional → developers may be surprised that `EarlyExitResponse` is part of the happy path rather than an error.

## Alternatives considered

- **Flag-based control (`ctx.should_continue`)** → rejected because forgetting to check produces silent wrong behaviour with no runtime signal.
