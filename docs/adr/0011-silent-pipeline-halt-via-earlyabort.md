# ADR-0011: Silent pipeline halt via EarlyAbort

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-05-28</p>

<p class="adr-meta">Extends: <a href="0010-exception-based-early-exit-with-guaranteed-terminal-stages.md">ADR-0010</a></p>

## Context

ADR-0010 introduced `EarlyExitResponse` for intentional short-circuits that still owe the user
a message, and a catch-all for unexpected exceptions. A third case emerged during implementation:
situations where the pipeline must stop entirely and sending any response — or running terminal
stages at all — would be wrong. The clearest example is platform-level consent revocation: if a
participant has blocked the bot or withdrawn consent at the platform layer, attempting to deliver
a response would fail or violate their intent, and persisting a message to the audit trail could
be misleading.

`EarlyExitResponse` is not suitable here because it always routes a message through terminal
stages including `ResponseSendingStage`. A distinct mechanism is needed for a true silent halt.

## Decision

We will introduce `EarlyAbort` as a second pipeline control-flow exception. When any core stage
raises `EarlyAbort`, the pipeline returns immediately — no `ctx.early_exit_response` is set, no
terminal stages run, and no user-facing message is generated. It is distinct from
`EarlyExitResponse` (which runs terminal stages) and from unexpected exceptions (which generate
an error message and run terminal stages before re-raising).

`EarlyAbort` is reserved for cases where the channel has determined that any further pipeline
activity — including sending and persisting — would be incorrect. It should not be used as a
general-purpose early exit; use `EarlyExitResponse` for any situation where the user should
receive a message.

## Consequences

- Stages can halt the pipeline completely without triggering sending or persistence, which is
  necessary for platform-layer consent and reachability failures.
- The distinction between `EarlyAbort` and `EarlyExitResponse` must be understood by stage
  authors; using the wrong one silently skips either the response or the audit trail.
- Terminal stages such as `ActivityTrackingStage` and `PersistenceStage` do not run on
  `EarlyAbort`, so session state is not updated for aborted interactions.

## Alternatives considered

- **Reuse `EarlyExitResponse` with a sentinel message**: Would still run terminal stages,
  causing `ResponseSendingStage` to attempt delivery to an unreachable participant. Rejected.
- **Boolean flag on context (`ctx.abort`)**: Requires every terminal stage to check the flag
  before running. Rejected for the same reason as the flag-based approach in ADR-0010.
