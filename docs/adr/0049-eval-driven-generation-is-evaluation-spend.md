# ADR-0049: Eval-driven generation is evaluation spend, billed without a trace

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-07-29</p>

<p class="adr-meta">Extends: <a href="0048-evaluation-spend-is-team-spend-not-entity-spend.md">ADR-0048</a></p>

## Context

ADR-0048 classified judge spend as `EVALUATION` and left one question open: is the bot generation an evaluation run drives the exercised chatbot's cost? It assumed generation was already recorded as chat by `OCSTracer`, so answering "no" meant reclassifying existing rows.

Nothing was recorded. `EvaluationChannel` has run with no tracers since October 2025, so eval generation writes no `Trace` and therefore no `UsageRecord`. The gap is the opposite of the one assumed: generation spend was missing from team totals rather than inflating a chatbot's.

Simply turning the tracer back on would resurrect what was deliberately switched off. Eval runs would write one `Trace` per evaluated message into a list with no platform filter. Those rows outlive their eval session, which `cleanup_old_evaluation_data` prunes while `Trace.session` is `SET_NULL`. Traces also drive error notifications, which eval failures would then fire.

## Decision

We will bill eval-driven generation as evaluation spend, and record it without a trace.

- **Generation is not the chatbot's cost.** The run exercised the chatbot, but not on anyone's behalf, so under the ADR-0048 rule the spend is the team's and never enters per-entity attribution.
- **A usage-only tracer** collects token usage and drains it into `UsageRecord`, and does nothing else: no `Trace`, no spans, no notifications. It is the eval channel's sole tracer.
- **Both halves of a run are attributed alike**: `source=EVALUATION`, the config as FK, the run id in `extra`, and the generation experiment resolved to its working version.
- **`OCSTracer` classifies by the session's platform.** Other traced paths reach an eval session — a static trigger firing on one does — so the rule is applied there rather than assumed to be the eval channel's alone.
- **No backfill.** Pre-existing rows are chat traffic; nothing is misclassified.

## Consequences

- Team-level cost rises for teams running generation evals, on spend that was previously unrecorded.
- No chatbot, participant or conversation gets cheaper than it looked before this change.
- The read path is untouched: keying on `source` alone (ADR-0048) already answers correctly for every consumer.
- An eval session's own cost page reports $0, since both halves of its spend are now `EVALUATION`; surfacing it is [#3981](https://github.com/dimagi/open-chat-studio/issues/3981).
- "What did evaluating this cost?" now covers a whole run, not just the judging.
- The admin report's halves diverge further: cost counts eval runs, tokens come from `Trace` and cannot, until [#3984](https://github.com/dimagi/open-chat-studio/issues/3984) moves token reporting onto `UsageRecord`.
- Eval runs still write no trace, so debugging a failing one stays as hard as it was.
- A tracer now exists that keeps no trace, so "tracer implies `Trace` row" is no longer true.

## Alternatives considered

- **Re-enable `OCSTracer` for eval channels** — rejected: cheapest to write, but pays for billing with unpruned trace volume, a polluted trace list, and notifications on eval failures.
- **Thread a usage collector through the channel and bot into the pipeline config** — rejected: the tracing service already carries exactly this from channel to LLM call, so the parameters would be redundant.
- **Leave generation as `source=CHAT` once recorded** — rejected: it bills a chatbot for traffic nobody asked it to serve, the distinction ADR-0048 exists to draw.
- **Classify in `OCSTracer` only** — rejected: it presumes the eval path writes a `Trace`, which is the thing being avoided.
