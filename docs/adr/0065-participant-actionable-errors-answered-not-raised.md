# ADR-0065: Participant-actionable errors are answered, not raised

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-09-11</p>

<p class="adr-meta">Extends: <a href="0010-exception-based-early-exit-with-guaranteed-terminal-stages.md">ADR-0010</a></p>

## Context

[ADR-0010](0010-exception-based-early-exit-with-guaranteed-terminal-stages.md) split pipeline exceptions into intentional short-circuits and unexpected failures, and the catch-all for the latter generates a participant-facing message, runs terminal stages, then re-raises so the failure reaches Sentry.

Some failures are neither. A participant attaches an image type the provider does not accept, or sends a voice note to a chatbot with no transcription configured. Nothing is broken: the participant can send something else and get an answer. `UserReportableError` existed for these but was not handled anywhere in the pipeline, so it fell to the catch-all. The generated reply was persisted and then discarded when the exception re-raised — the trace was marked as errored, the task failed, and the participant was shown the raw exception text by whichever channel happened to catch it, or nothing at all.

The name was also the wrong axis. "Reportable" described where the message was sent, which told an author nothing about when to raise it.

## Decision

We will treat `UserActionableError` as pipeline control flow, alongside `EarlyExitResponse` and `EarlyAbort`.

- Raise it when the participant can fix what went wrong by changing what they send. That is the whole test: not whether the message is safe to show, but whether the participant has an action available.
- The pipeline generates a participant-facing message from it via `EventBot`, sets `ctx.early_exit_response`, runs terminal stages and returns normally. It is never re-raised.
- It is a control-flow signal in `ProcessingStage.__call__`, so a stage's span is not marked as errored and the occurrence does not count on operator error-rate charts.
- Its message text is written for the participant, because it is fed to the LLM that composes their reply.
- Anything the participant cannot act on — a provider outage, a revoked key, a bug — must use a different exception and keep the catch-all's re-raise.

A chatbot with no transcription provider is in scope: the participant's action is to send text instead. A transcription backend that fails is not, and raises `AudioTranscriptionException`.

## Consequences

- One participant-facing reply per occurrence, sent and persisted by the terminal stages on every channel, instead of a channel-specific error string.
- These stop failing Celery tasks and reaching Sentry, so an operator loses the error-rate signal for a rising number of rejected attachments; team notifications and span outputs remain.
- Callers no longer need to catch it. The webchat task's `user_facing_error` flag lost its only producer.
- Misclassifying a genuine fault as `UserActionableError` now hides it. The exception's docstring carries the rule.

## Alternatives considered

- **Keep the re-raise and catch it per channel** → what the code did; every channel needs its own handler, and the generated reply is computed then thrown away.
- **Fold it into `EarlyExitResponse`** → that carries a literal response string, so each raise site would have to write participant-facing wording instead of letting `EventBot` phrase it in the participant's language.
- **Treat it as a configuration error** → those reply with canned text and skip the LLM, which loses the specifics the participant needs to act on.
- **Keep the name `UserReportableError`** → "user" is ambiguous between the team member and the participant, and "reportable" describes delivery rather than the condition for raising.
