# ADR-0051: One set of activity-metric definitions across usage surfaces

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-08-06</p>

## Context

Sessions, messages and participants are computed in two places. The dashboard counts a session as active when any message of any type falls inside a closed `[start, end]` window; the v2 usage API counts sessions created inside a half-open `[start, end)` window and drops sessions still in `SETUP`. Message totals include `system` messages on one surface and not the other. Evaluation-harness activity counts on the API side and not the dashboard side. "Active participant" has four implementations across the two surfaces, disagreeing on whether an AI message or a `system` message makes a participant active.

The result is that the same team and the same window produce different numbers depending on which surface someone reads, with nothing labelling which definition is in play. Within the API itself, platform-grouped message rows exclude evaluations while the ungrouped total includes them, so grouped rows do not sum to the total for teams that run evaluations.

Cost and tokens already share one implementation (`apps/cost_tracking/services/reporting.py`). Activity metrics did not. This ADR is part of the wider convergence tracked in issue #3905.

## Decision

We will define each activity metric once, in `apps/usage_metrics/`, and have every surface read it from there.

- **`sessions_active`** - sessions with at least one human or AI message in the window. Sessions still in `SETUP` and evaluation-harness sessions do not count.
- **`sessions_started`** - sessions created in the window, on the same two exclusions.
- **`sessions_in_setup`** - sessions created in the window and still in `SETUP`. `sessions_started + sessions_in_setup` is every non-evaluation session created in the window, so setup drop-off stays countable.
- **`messages`** - human and AI messages only, evaluation sessions excluded. `total` is `human + ai`; `system` messages are internal and are not conversation turns.
- **`active_participants`** - distinct participants who authored at least one human message in the window. A participant who only received AI output was not active.
- **Sessions still in `SETUP` are excluded from every activity metric**, not only from the session counts. `SETUP` is the state a session occupies until its first message: the consent flow moves it to `PENDING` and then `ACTIVE`, and a chatbot without conversational consent activates it straight away, so a session resting at `SETUP` has no conversation in it. Counting its turns or its author while `sessions_active` drops the session would put a ratio's numerator and denominator on different universes, which the ratios rule below forbids.
- **Windows are half-open `[start, end)`** on every surface, so an instant on the boundary is counted exactly once across adjacent periods. A date-range picker whose end date should be fully included resolves that date to the start of the following day.
- **`ExperimentSession.platform`** is the sole discriminator for evaluation-harness activity. `ExperimentChannel.platform` is a separate nullable column, nothing keeps the two aligned, and they can disagree on a row.
- **`include_archived`** applies to experiment *enumeration* only. Activity metrics count archived-chatbot activity regardless, because that activity happened and the spend was real.

Two rules govern how these metrics may be combined. Running example: a team spends $100 in June, $80 from chat and $20 from evaluation runs.

- **Ratios.** A ratio's numerator and denominator must describe the same activity. Cost-per-message is the $80 of chat spend over chat messages. Dividing the $100 by chat messages would bill evaluation spend to conversations that did not incur it. Per-message, per-session and per-token ratios always use chat-source spend over chat activity, and a surface showing the $100 inclusive total never captions it "per message" or "per session".
- **Totals versus breakdowns.** The headline total answers "what did the team spend?" and counts everything. A per-entity breakdown answers "which chatbot spent it?" and only includes spend attributable to that entity. The per-chatbot table for June sums to $80, not $100: the $20 of evaluation spend belongs to the team and not to any chatbot (ADR-0048). Rows from archived chatbots, or with no chatbot recorded, behave the same way. That gap is by design and is not to be closed by hiding it or by spreading evaluation spend across bots.

## Consequences

Both surfaces move to the same numbers in one change, with no flag - a flag would mean maintaining two definitions of the metric it was meant to retire.

Numbers visibly change:

- Dashboard session counts drop sessions whose only in-window activity is a `system` message, and drop sessions still in `SETUP`.
- Dashboard message totals drop `system` messages, and both surfaces drop turns belonging to sessions still in `SETUP`. In practice a session resting at `SETUP` holds no conversation, so this moves few or no rows; it is what keeps the per-session and per-participant ratios on one universe.
- Dashboard active-participant counts drop participants whose only in-window activity is AI or `system` messages, on the overview stat and the session-analytics series. The active-participants chart already used this definition and does not move.
- The API's `messages` and `participants` metrics drop evaluation activity, and `participants` drops participants whose only in-window activity is AI messages. Grouped rows now sum to the ungrouped total.
- Instants on a window boundary stop being counted in two adjacent periods.

`sessions_in_setup` is new. It is exposed through `usage_metrics` and has no UI surface in this block.

The usage API has very low, internal-only usage, so the changes ship with a changelog entry listing every visible change rather than a consumer migration.

## Alternatives considered

- **A feature flag over the two definition sets** - rejected: it would keep both definitions alive indefinitely and make "which number is right?" a per-team question.
- **Keeping `sessions_active` and `sessions_started` as one metric** - rejected: they answer different questions - "who used the product in this period?" versus "how many conversations began in it?" - and a single count would silently serve one question to someone asking the other. Two named metrics, each labelled at its surface, is the point.
- **A separate "participants reached" metric** counting participants who received AI output - rejected: session counts already answer that, and a second participant metric would reintroduce the ambiguity this ADR removes.
- **Excluding archived-chatbot activity from the metrics** - rejected: the activity happened and the spend was real; hiding it would make the totals disagree with the cost panel.
