# ADR-0048: Evaluation spend is team spend, never entity spend

<span class="adr-status adr-status-proposed">PROPOSED</span>

<p class="adr-meta">Author: Open Chat Studio · Created: 2026-07-28</p>

## Context

An evaluation run spends money in two ways. The bot generation it drives is ordinary traced traffic, so `OCSTracer` records it as `UsageRecord` rows like any chat turn. The evaluator's own judge calls bypass the tracer entirely — they were previously not recorded at all, so judge spend was invisible.

Once judge calls are recorded, every consumer of `UsageRecord` has to answer a question none of them had a way to express: does evaluation spend count as the team's usage, and does it count as a *chatbot's* usage? The answer differed by accident of implementation. The admin report's token half excluded the evaluations platform while its cost half included it, so the two could not reconcile. The dashboard's per-chatbot cost column and the v2 usage API's grouped cost counted eval-driven generation as chatbot spend. Nothing distinguished eval rows except which columns happened to be null, and that discriminator does not work: judge rows from a generation run carry an experiment and a session, while session-mode runs — where generation is barred (`EvaluationConfigForm`) — carry neither.

## Decision

We will make the classification a first-class column and adopt one rule for reading it.

- **`UsageRecord.source`** (`chat` | `evaluation`, default `chat`) is set explicitly by every writer. It is the only sanctioned discriminator; null columns are not.
- **`UsageRecord.evaluation_config`** (nullable, `SET_NULL`) links eval rows to the eval definition that caused them. It points at the config, not the run, because runs are pruned (`cleanup_old_evaluation_data`) while the config is the thing whose cost someone asks about. The run id is kept in `extra` for forensics.
- **The rule: evaluation spend is the team's spend, but it is never a chatbot's, a participant's, or a conversation's spend.** Team-level totals count every source; any read that attributes cost to a single entity counts `chat` only. `_attributable_records` in `services/reporting.py` is the single enforcement point, so the dashboard and the usage API cannot drift apart.
- **Billing views count everything, without a per-source split.** The team dashboard panel and the admin cross-team report keep inclusive totals. Surfacing the evaluation share to users is deliberately out of scope here and belongs with cost breakdowns generally; `source` is what makes it possible later.

## Consequences

- Per-chatbot and per-session cost stop being inflated by eval activity, in the dashboard and the v2 usage API alike.
- Team-level totals rise where eval spend was previously unrecorded, with nothing on those surfaces yet explaining why — the split is recorded but not shown.
- In the v2 usage API, grouped cost/token rows no longer sum to the aggregate block for a team that runs evals. This extends an existing documented gap (rows with a NULL group field were already excluded) but is a visible change for API consumers who reconcile the two.
- "What did evaluating this cost?" becomes answerable per config via the FK, and per run via `extra`.
- Every future writer of `UsageRecord` must choose a source. The `chat` default keeps the migration additive for existing rows (all tracer-written), at the cost of a new writer silently inheriting `chat` if it forgets.
- Deleting an eval config keeps its billing rows and their classification; only the drill-down link is lost.

## Alternatives considered

- **Keep the JSON marker (`extra["source"]`)** — rejected: the discriminator would be the least queryable column on one of the highest-volume tables, and every consumer would hand-roll a JSONB filter.
- **An `evaluation_run` FK as the marker** — rejected: runs are pruned, so `SET_NULL` would silently reclassify eval rows as chat and `PROTECT` would block eval cleanup.
- **Infer eval rows from `trace_id IS NULL`** — rejected: it encodes "which writer wrote this", not "what the spend was for", and would break the moment another untraced writer appears.
- **Exclude eval spend from the v2 API's totals too**, making the API internally consistent — rejected: the API would stop reporting what a team actually spent, which is what billing reconciliation needs it for.
- **Attribute session-mode judge spend to the session under evaluation** — rejected for now: it would make a real participant's conversation look more expensive than it was, and no consumer needs that drill-down yet.
