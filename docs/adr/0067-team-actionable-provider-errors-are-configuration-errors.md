# ADR-0067: Team-actionable provider errors are configuration errors

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Simon Kelly · Created: 2026-09-16</p>

<p class="adr-meta">Extends: <a href="0065-participant-actionable-errors-answered-not-raised.md">ADR-0065</a></p>

## Context

[ADR-0065](0065-participant-actionable-errors-answered-not-raised.md) closed one gap in the catch-all and named the next one: "anything the participant cannot act on — a provider outage, a revoked key, a bug — must use a different exception". It did not say which, so a revoked key still travelled as `openai.AuthenticationError` and reached the catch-all alongside genuine bugs.

The exceptions this produces are the loudest class of unresolved issue in the project, and they share a shape: the provider rejects the call, neither an operator nor an OCS developer can fix it, and no retry will ever succeed. An OpenAI account with no credits accounted for 469 events from a single team; a withdrawn Gemini model, an Anthropic usage cap, a mistyped key and a conversation past the context window make up most of the rest. Each failed its Celery task and paged operators about a team's billing.

They also hide inside the wrong status codes. OpenAI reports an exhausted balance as a 429, indistinguishable by status from a rate limit, so the retry policy burned the full backoff on every message before failing.

The pipeline already has the tier these belong in. `CONFIGURATION_EXCEPTIONS` — a deprecated model, an unreachable node, a broken template — replies with canned text, logs a warning, and is never re-raised. A provider that will not serve the chatbot until someone acts is the same condition arriving from outside.

## Decision

We will classify team-actionable provider failures as `ProviderConfigurationError`, a member of `CONFIGURATION_EXCEPTIONS`.

- The test is who holds the fix. A team member can add credit, replace a key, pick a different model or shorten the history; a participant cannot, and neither can an operator or an OCS developer. Transient faults — rate limits, overload, connection errors — fail this test and keep their native SDK type.
- `translate_provider_error` does the classification, reading provider error codes and, where Anthropic offers nothing else, the message text. It is the single source of truth: `should_retry_exception` consults it first, so nothing classified here is ever retried.
- Translation happens at the node boundary, in `BasePipelineNode.process` and the router function, which is outside the LangGraph retry policy. A transient error therefore still reaches the retry policy as its native type; a terminal one is converted only once retrying is no longer on the table.
- The provider's own wording is carried into the message, because that wording is what tells the team which account and which model to fix.
- The trace is still marked as errored and the team is still notified. This is the difference from ADR-0065: a participant-actionable error is not a failure of the chatbot, whereas a chatbot that cannot reach its model is broken until someone acts.

## Consequences

- These stop failing Celery tasks and reaching Sentry. Operators lose the Sentry view of a team's billing problems and gain the in-app, email and Slack notification the team can act on.
- Misclassification hides a genuine fault behind canned text. Anthropic billing errors are the weak spot, because they can only be recognised by their wording. If Anthropic rewords one, it stops being recognised: the team still gets notified, but the message says something went wrong rather than naming the billing problem.
- A provider's 404 is read as a missing model. An assistant or file the provider has deleted reaches the team with model wording.
- Google is classified for credentials and missing models but not for billing, because it reports an exhausted balance and a per-minute rate limit alike as `ResourceExhausted`. Those keep retrying and still reach the catch-all; adding any further provider means extending the classification the same way.

## Alternatives considered

- **Translate at the LLM call sites** → each new invoke site has to remember, and the three that exist already disagree on which errors they handle; the node boundary catches them all.
- **Reuse `UserActionableError`** → it deliberately leaves the trace green and fires no notification, which is exactly wrong for a chatbot that is down.
- **A new tier alongside configuration errors** → the handling would be identical to `CONFIGURATION_EXCEPTIONS`, including the reason for skipping the LLM-generated reply: the LLM is the thing that is unavailable.
- **Keep ignoring the issues in Sentry** → what was being done; it is per-issue, resets when the message text changes, and the team is never told.
