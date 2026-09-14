# ADR-0065: Participant-actionable errors are answered, not raised

<span class="adr-status adr-status-accepted">ACCEPTED</span>

<p class="adr-meta">Author: Chris Smit · Created: 2026-09-11</p>

<p class="adr-meta">Extends: <a href="0010-exception-based-early-exit-with-guaranteed-terminal-stages.md">ADR-0010</a></p>

## Context

[ADR-0010](0010-exception-based-early-exit-with-guaranteed-terminal-stages.md) split pipeline exceptions into intentional short-circuits and unexpected failures, and the catch-all for the latter generates a participant-facing message, runs terminal stages, then re-raises so the failure reaches Sentry.

Some failures are neither. A participant attaches an image type the provider does not accept, or sends a voice note to a chatbot with no transcription configured. Nothing is broken: the participant can send something else and get an answer. `UserReportableError` existed for these but was not handled anywhere in the pipeline, so it fell to the catch-all. The generated reply was persisted and then discarded when the exception re-raised — the trace was marked as errored, the task failed, and the participant was shown the raw exception text by whichever channel happened to catch it, or nothing at all.

The name was also the wrong axis. "Reportable" described where the message was sent, which told an author nothing about when to raise it.

## Decision

We will treat `UserActionableError` as control flow rather than failure, the way `EarlyExitResponse` already is.

- Raise it when the participant can fix what went wrong by changing what they send. That is the whole test: not whether the message is safe to show, but whether the participant has an action available.
- The pipeline generates a participant-facing message from it via `EventBot`, sets `ctx.early_exit_response`, runs terminal stages and returns normally. It is never re-raised.
- No span it crosses is marked as errored, and the trace stays `SUCCESS`, so the occurrence does not count on operator error-rate charts and fires no trace-error notification. This is enforced in three places, because the exception can be raised at any depth: `ProcessingStage.__call__` for the stage's own span, `TracingService.span` for the spans opened inside the bot run, and `OCSCallbackHandler._capture_error` for LangChain's chain/LLM/tool error callbacks.
- The stage that raises it must not report a failure either: `QueryExtractionStage` skips its team notification for this class, since nothing was tried and failed.
- Its message text is written for the participant, because it is fed to the LLM that composes their reply. The prompt relays that text and asks what the participant can do now; it does not offer waiting, which is the one thing that cannot help.
- Anything the participant cannot act on — a provider outage, a revoked key, a bug — must use a different exception and keep the catch-all's re-raise.

A chatbot with no transcription provider is in scope: the participant's action is to send text instead. A transcription backend that fails is not, and raises `AudioTranscriptionException`.

### Deferring the raise until the turn is recorded

`QueryExtractionStage` cannot raise where it stands. A voice note is real input and belongs in the chat history and on the trace even when nothing could be read out of it, but the stage that records it, `ChatMessageCreationStage`, runs after it. Raising in place stops the pipeline first, so the participant gets a reply about a message that is missing from their own history.

The stage therefore holds the exception on the context, leaves `ctx.user_query` empty, and lets `ErrorGuardStage` raise it once the turn is on record. Both classes defer: a `UserActionableError` and a genuine `AudioTranscriptionException` travel the same way, and the policy above is what then decides which is answered and which is re-raised.

That creates an ordering invariant no single file shows: **every pipeline that runs `QueryExtractionStage` must also run `ErrorGuardStage`, positioned after `ChatMessageCreationStage`.** A pipeline that extracts a query without the guard swallows the error completely — the participant gets no reply at all, and a transcription fault is never reported. Channels assemble their own stage lists, so this is a convention the framework cannot impose; a test enumerates every registered channel class and asserts both the presence and the position.

## Consequences

- One participant-facing reply per occurrence, generated once and delivered by the terminal stages each channel has, instead of a channel-specific error string. `EvaluationChannel` has no persistence or sending stage, and `ApiChannel` and `WebChannel` persist but return the reply rather than sending it.
- These stop failing Celery tasks and reaching Sentry, so an operator loses the error-rate signal for a rising number of rejected attachments; team notifications and span outputs remain.
- Callers no longer need to catch it. The webchat task's `user_facing_error` flag lost its only producer.
- Misclassifying a genuine fault as `UserActionableError` now hides it completely: no Sentry event, no errored trace, no notification. The exception's docstring carries the rule.
- `invoke_with_image_error_translation` is the sharpest instance of that risk. It reads provider codes that describe the bytes the provider fetched from our own `download_link`, so a storage or permissions fault on our side can present as a rejected image and be reported to the participant as their problem.

## Alternatives considered

- **Keep the re-raise and catch it per channel** → what the code did; every channel needs its own handler, and the generated reply is computed then thrown away.
- **Fold it into `EarlyExitResponse`** → that carries a literal response string, so each raise site would have to write participant-facing wording instead of letting `EventBot` phrase it in the participant's language.
- **Treat it as a configuration error** → those reply with canned text and skip the LLM, which loses the specifics the participant needs to act on.
- **Keep the name `UserReportableError`** → "user" is ambiguous between the team member and the participant, and "reportable" describes delivery rather than the condition for raising.
