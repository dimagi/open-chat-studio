# 02 — Source and Routing Rules

> The data-plumbing tabs on an Assessment detail page: **Source** (where items come from — batch dataset or continuous filter) and **Routing** (the rules engine that fires actions on Scores, lifecycle events, flags, disagreement, or applied tags). These two tabs are paired because together they describe the *flow* of an Assessment — what flows in, what fires on the way through.
>
> **Anchored to** [`../unified-assessment.md`](../unified-assessment.md): D-5 (continuous Assessments produce no Runs), D-12 (reuse the filter language, not the `FilterSet` model), D-13 (Score targets), D-14 (`AppliedRoutingRule` audit), D-15 (lifecycle hooks, `AppliedSourceFilter`), D-16 (`HUMAN_DISAGREEMENT` + authoritative).
>
> **Out of scope** — covered by other briefs: Assessment/Schema/Scorer config (brief 01); the *act of reviewing* an escalated item (brief 03); the *results* of running (brief 04). The Routing tab here shows the rule definition + audit; consequences of rules firing belong to the action's target tab.

## User stories addressed

- Story 4 — *Continuous LLM-judge monitoring*: continuous `Source` with a filter expression.
- Story 5 — *Human queue, judge-flagged*: `RoutingRule(SCORE_VALUE → ESCALATE_TO_HUMAN_SCORER)`.
- Story 6 — *Human queue, user-feedback-flagged*: continuous `Source` with `filter_query_string` reading `USER_FEEDBACK` Scores.
- Story 10 — *Second-pass review for uncertain items*: `RoutingRule(HUMAN_FLAG → ESCALATE_TO_HUMAN_SCORER)`.
- Story 9 (adjudication side) — *Reviewer disagreement*: `RoutingRule(HUMAN_DISAGREEMENT → ESCALATE_TO_HUMAN_SCORER(mark_authoritative=True))` (D-16).
- Backlog #4 (continued) — *Evals tag sessions for filtering*: `RoutingRule(SCORE_VALUE → EMIT_TAG)`.
- Backlog #5 — *Cross-Assessment escalation*: `RoutingRule(LIFECYCLE_EVENT → ADD_TO_QUEUE)`.

## Information architecture

```
Assessment detail
├── Overview      (brief 01)
├── Source        ← THIS BRIEF
├── Scorers       (brief 01)
├── Routing       ← THIS BRIEF
├── Runs          (brief 04 — batch only)
├── Trends        (brief 04 — continuous only)
├── Concordance   (brief 04)
└── Reviews       (brief 03)
```

The Source tab is **single-page**; mode determines the form. The Routing tab is a **list-with-modal-editor** — list of rules at the top, "Add rule" opens an editor that pivots on `trigger_kind` × `action_kind`.

## Screens

### S7 — Source tab

**Purpose**: configure how items enter the Assessment. Mode is fixed at creation (S2 step 1, brief 01); this tab is for the rest of the configuration plus operational visibility.

**Primary user**: Bot Builder, Team Lead.

The tab pivots on the Assessment's mode (set at creation):

#### S7a — Batch mode source

For an Assessment with **no `Source.filter_query_string`** (D-5: items are added at config time; runs are explicit; no lifecycle hooks).

**Sections** (top to bottom):

1. **Dataset summary card**
   - Dataset name, item count, granularity (`session` / `message` per D-13), CSV-import status if applicable.
   - "Open dataset" link to the dataset editor (carried-forward from existing `templates/evaluations/dataset_edit.html` — out of scope for this brief).
2. **Item population** (the three population mechanisms — UI affordances on the same underlying dataset, not separate sources):
   - **Import sessions** — opens the sessions picker (replaces `add_items_from_sessions.html`). Filter sessions by experiment, date range, tag, participant. Bulk-select, "Add N sessions" button.
   - **Import CSV** — replaces `dataset_create_form.html`. Column-mapping step matches `create_dataset_from_csv_task` shape; preserve the existing UX, just relabel "Evaluation dataset" → "Assessment dataset".
   - **Add manual item** — minimal input/output/context form, append to dataset.
   - **Import from another Assessment's results** — opens a picker for source Assessments with `≥1 HumanScorer` (carries forward FR-8.2 / today's `ImportFromAnnotationQueueForm`). One-shot copy; clearly labelled as such; explainer copy: *"This copies items at the moment of import. For live coupling, add a scorer to the source Assessment instead."*
3. **Bot generation** (optional, per FR-3.6) — single toggle "Generate bot responses before scoring". When on: pick experiment (FK), version selection moves to `AssessmentRun` per D-3 (so no version field here).
4. **Deduplication** — show counts of items already in *other* Assessments using the same dataset (per FR-2.7). Read-only awareness, no enforcement.

**States**:
- *Empty dataset*: three large CTAs side-by-side (Import sessions · Import CSV · Add manual item) — no awkward 0-row table.
- *Mid-import*: progress chip on the affected section (CSV parse, bulk-add).

#### S7b — Continuous mode source

For an Assessment with **`Source.filter_query_string` set** (D-5: items stream in via lifecycle hooks; no `AssessmentRun` rows; aggregates are on-demand).

**Sections**:

1. **Granularity and target**
   - Read-only chip showing `session` or `message` (set at creation; changing granularity requires creating a new Assessment).
   - One-liner clarifying what gets scored: *"One Score row per `ExperimentSession`"* (session-granularity) or *"One Score row per `Trace` (LLM call / pipeline execution)"* (message-granularity, per D-13). Mention v1 latency caveat (D-15): *"Per-message scoring runs at session end."*
2. **Filter editor**
   - Filter-language editor (reuse the parser from [`apps/filters/`](../../../apps/filters/), per D-12). UI affordances:
     - **"Copy from saved filter"** dropdown — pulls a saved `FilterSet`'s `filter_query_string` into the source as a starting point. After copy, the Source owns its own string; further edits to the originating `FilterSet` don't propagate.
     - Visual filter builder fallback if free-text filter intimidates non-technical users — uses the same parser, just renders the predicates as chips with field/op/value selectors.
   - **Live preview**: "Show 10 matching items from the last 24h" — read-only sample with deep links to the session/trace.
3. **Sample rate** (FR-2.3 / FR-3.4) — decimal 0.0–1.0 (default 1.0). Slider + percentage display. Helper: *"Of items matching the filter, what fraction to score. Lower this to control cost on high-volume signals."*
4. **Idempotency note** (read-only callout): *"Each target is scored at most once per Assessment (uniqueness on `(assessment, target, name, source)`)."* Per the Score unique index in the design doc.
5. **Pause/resume** — toggle visible at the top of the section. When paused, lifecycle hooks no-op for this Assessment. State is reversible.
6. **Operational health card** — three numbers per D-15:
   - Latest `Score.created_at` (e.g. *"Latest score 4 min ago"*).
   - 24h Score count grouped by source (small bar/sparkline).
   - 24h `AppliedSourceFilter` failure count, broken down by `outcome` (`FILTER_NO_MATCH` / `DEDUP_SKIP` / `SAMPLE_ROLLED_OUT` / `SCORER_ERROR`). Each failure type links to a filtered audit view (S7c).
   - "Why didn't this session get scored?" — small search box accepting a session id; runs a point query against `AppliedSourceFilter` to show the answer.

**States**:
- *Just configured, no traffic yet*: empty operational card with copy *"Waiting for first matching event."*
- *Paused*: dimmed sections, banner *"This source is paused — no Scores are being written."*
- *Many `SCORER_ERROR` rows*: alert at top of card with link to error breakdown.

#### S7c — `AppliedSourceFilter` audit log (sub-view)

Reachable from S7b's operational card. Read-only table.

**Columns**: timestamp, event type, target (deep link), outcome, error message (if any).

**Filters**: outcome type, date range, event type.

**Empty state**: *"No skipped or failed dispatches in this window. Successful Scores are visible on the Scorers / Trends tabs."* (Per D-15 — successes are not audited.)

### S8 — Routing tab

**Purpose**: define the rules engine for an Assessment — what triggers what. Replaces the per-evaluator-only `EvaluatorTagRule` UI today; same general shape (rule list + per-rule editor) but with a richer trigger × action surface.

**Primary user**: Team Lead (mostly), Bot Builder (for `EMIT_TAG` rules).

#### S8a — Rule list

**Information per row**:
- Rule name (auto-generated default: `<trigger summary> → <action summary>`).
- **Trigger chip** — colour-coded per kind:
  - `SCORE_VALUE` — judge icon, e.g. *"score.accuracy < 0.5"*.
  - `LIFECYCLE_EVENT` — event icon, e.g. *"SESSION_ENDED"*.
  - `HUMAN_FLAG` — flag icon, e.g. *"reviewer flagged"*.
  - `HUMAN_DISAGREEMENT` — split-arrow icon, e.g. *"disagreement on tone (stdev > 0.3)"* (D-16).
  - `TAG_APPLIED` — tag icon, e.g. *"tag 'emergency' applied"*.
- **Action chip** — colour-coded per kind:
  - `EMIT_TAG` — *"tag 'low-quality'"*.
  - `ESCALATE_TO_HUMAN_SCORER` — *"send to `<HumanScorer name>`"*, with `mark_authoritative=True` indicator if set.
  - `NOTIFY` — *"notify `<channel>`"*.
  - `ADD_TO_QUEUE` — *"queue in Assessment `<other>`"* (cross-Assessment per FR-11).
- **Sample policy** — chip if not `EVERY` (`THRESHOLD` / `RANDOM_N_PERCENT(p)`).
- **Status** — active / paused per rule.
- **Last fired** — relative timestamp; click to jump into `AppliedRoutingRule` audit filtered to this rule (S8c).
- **Fire count (last 24h / last 7d)** — sanity-check signal.

**Actions**: "Add rule" (top-right) · per-row edit · per-row duplicate · per-row pause/resume · per-row delete.

**Empty state**: *"No routing rules yet. Add a rule to react to scores, lifecycle events, or human flags."* with three "Common patterns" presets (see S8b below).

#### S8b — Rule editor (modal/drawer)

The editor pivots on `trigger_kind` × `action_kind`. To keep the UI tractable, present them as **two cascading selects + dynamically-rendered config blocks**, not a 5 × 4 grid.

**Top of form**:
- **Name** (auto-defaulted; editable).
- **Preset chips** ("Common patterns") to pre-fill the form for the matrix the design doc enumerates:
  - "Tag low-quality sessions" (SCORE_VALUE → EMIT_TAG)
  - "Escalate to human review" (SCORE_VALUE → ESCALATE_TO_HUMAN_SCORER)
  - "Random calibration sample" (SCORE_VALUE + RANDOM_N_PERCENT → ESCALATE_TO_HUMAN_SCORER)
  - "Second-pass on reviewer flag" (HUMAN_FLAG → ESCALATE_TO_HUMAN_SCORER)
  - "Adjudicate disagreement" (HUMAN_DISAGREEMENT → ESCALATE_TO_HUMAN_SCORER, `mark_authoritative=True`)
  - "Alert on emergency tag" (TAG_APPLIED → NOTIFY)
  - "Cross-Assessment escalation" (LIFECYCLE_EVENT → ADD_TO_QUEUE)

##### Trigger block

A `trigger_kind` select + a config sub-block per kind:

- **`SCORE_VALUE`**:
  - Field (dropdown from the Assessment's `AssessmentSchema` field names — already constrained to scorer `output_fields`).
  - Operator (`<`, `≤`, `=`, `≥`, `>`, `∈` for categorical) — operator list pivots on the field's `data_type`.
  - Value (input control pivots on `data_type` — number input for numeric, choice picker for categorical).
  - Source filter (optional multi-select): *"Only trigger on scores from these sources"* — `LLM_JUDGE`, `PROGRAMMATIC`, `HUMAN_REVIEW`, `USER_FEEDBACK`, `SYSTEM`. Useful for Story-6-style "fire only on user feedback".
- **`LIFECYCLE_EVENT`**:
  - Event type select — `SESSION_ENDED` / `AUTOMATED_RUN_FINISHED` / `USER_FEEDBACK_RECEIVED` / `TAG_APPLIED`. (Per D-15: all four available to routing rules; only `SESSION_ENDED` drives continuous *scoring* in v1.)
  - Optional payload filter (e.g. for `AUTOMATED_RUN_FINISHED`, restrict to a specific scorer).
- **`HUMAN_FLAG`**:
  - Optional flag-reason match (substring or choice — flag reasons are free-text today).
  - Source `HumanScorer` (default: any in the same Assessment).
- **`HUMAN_DISAGREEMENT`** (D-16):
  - Field (dropdown).
  - For numeric: stdev threshold (number input, default suggested per field).
  - For categorical: behaviour fixed (non-unanimous) — read-only explainer.
  - For string: disable trigger with inline note (*"Strings don't aggregate; disagreement isn't defined."* per FR-6.3).
- **`TAG_APPLIED`**:
  - Tag picker (multi-select against the team's `Tag` rows).
  - Target type filter (optional: `Chat` / `ChatMessage`).

##### Action block

An `action_kind` select + a config sub-block per kind:

- **`EMIT_TAG`**:
  - Tag picker (or create new — inline against the team's `Tag` catalogue).
  - Tag target — read-only chip showing what the tag attaches to based on Source granularity (session-granularity → `session.chat`; message-granularity → `ChatMessage`). Per the existing `EvaluatorTagRule` semantics.
- **`ESCALATE_TO_HUMAN_SCORER`**:
  - HumanScorer picker (only HumanScorers within the same Assessment listed — within-Assessment escalation is the default per FR-8.1).
  - `mark_authoritative` toggle (D-16) — only enabled when the trigger is `HUMAN_DISAGREEMENT`; reset and hidden otherwise to prevent footguns.
  - **Sample policy** — see below.
  - Optional "Send to a different reviewer than the originator" toggle — relevant for `HUMAN_FLAG` (Story 10) and `HUMAN_DISAGREEMENT` (adjudicator role). Default on for those triggers.
- **`NOTIFY`**:
  - Channel — leverages existing OCS notification primitives (out-of-scope to specify the channel set here; reuse whatever `apps/events/EventAction` supports plus whatever explicit notification surfaces exist).
  - Template — short text with template variables (rule name, trigger details).
- **`ADD_TO_QUEUE`** (cross-Assessment, per FR-11):
  - Target Assessment picker (only Assessments with ≥1 HumanScorer listed).
  - Target HumanScorer picker (within the chosen Assessment).
  - **Warning callout** (per FR-11.2): *"This is fire-and-forget. Scores produced by the receiving Assessment do not appear in this Assessment's concordance. Within-Assessment escalation is what you want for concordance."* Link to *"Use within-Assessment escalation instead"* which switches the action to `ESCALATE_TO_HUMAN_SCORER`.

##### Sample policy block

Always visible (FR-3.8 / D-doc rule semantics): `EVERY` (default) · `THRESHOLD` · `RANDOM_N_PERCENT(p)`.

- For `RANDOM_N_PERCENT`: percentage input + helper *"Rolled deterministically per `(rule_id, triggered_by_id)` — re-firing the same trigger gets the same outcome."* (Matches the design doc's idempotency guarantee.)
- For `THRESHOLD`: only meaningful when the trigger has a comparable axis (typically `SCORE_VALUE` numeric); UI hides or disables otherwise.

##### Validation in the editor

- `mark_authoritative=True` requires trigger = `HUMAN_DISAGREEMENT` (or admin manual override per D-16). UI enforces.
- `ESCALATE_TO_HUMAN_SCORER` requires the target HumanScorer's `output_fields` to overlap with the triggering field for `SCORE_VALUE` / `HUMAN_DISAGREEMENT` triggers — block with inline error if not.
- `SCORE_VALUE` triggers must reference a field actually produced by some scorer in this Assessment (i.e. is in some scorer's `output_fields` per D-10). Block otherwise with helper *"No scorer in this Assessment produces `<field>`."*
- `RoutingRule(SCORE_VALUE)` on a field whose `data_type=STRING` is disabled (consistent with disagreement and aggregation).
- Cross-Assessment `ADD_TO_QUEUE` is only available when the source has a granularity matching the target Assessment's first HumanScorer item type — block with helper otherwise.

**Live preview panel** (right-side drawer if there's room): *"This rule would have fired N times in the last 24h."* Backed by a dry-run against historical Scores. Helps build confidence before turning a rule on.

#### S8c — `AppliedRoutingRule` audit log

Reachable from "Last fired" on any rule row in S8a, or from a per-Assessment top-level "Rule history" link.

**Purpose**: answer "did this rule fire, and what did it produce?" Per D-14, every firing produces an `AppliedRoutingRule` row.

**Columns**: timestamp, rule (chip), `triggered_by` (deep link — `AutomatedResult` / `Review` / `ExperimentSession` / `CustomTaggedItem` depending on trigger), `outcome` (deep link — `CustomTaggedItem` / `ReviewItem` / notification / etc.), context summary (the JSON `context` rendered as key-value chips: which field matched, what value, sample-roll outcome).

**Filters**: rule, outcome type, date range, trigger kind.

**Why this view exists**: when a routing rule misbehaves (e.g. fires too often, escalates the wrong items), the audit trail must be inspectable. The unique constraint on `(rule, triggered_by, outcome)` (D-14) means each firing is unambiguous.

## Cross-cutting concerns

### Permissions

- **Create/edit/delete routing rules** — team admin.
- **View routing rules and audit log** — anyone on the team with the `ASSESSMENTS` flag.
- **Pause/resume a rule** — team admin.
- **Pause/resume continuous Source** — team admin (this is the kill-switch for an Assessment writing Scores in production).

### Idempotency and re-fires (operational copy that should appear in UI)

The design doc commits to consumer-side idempotency, not at-most-once dispatch (D-15). The UI should surface this in two places:

1. **On the SourceFilter editor (S7b)**, the dedup callout: *"Each target is scored at most once per Assessment."*
2. **On the Routing rule editor (S8b) sample-policy helper**, the determinism callout: *"Same trigger + same rule = same outcome. Safe to re-fire."*

This is the kind of guarantee users will lean on; the UI should be quiet about *how* it works (constraints + hashes) but loud about *that* it works.

### "All matching rules fire" — no priority field

Per the design doc's execution semantics: all matching rules fire; there is no priority/short-circuit. The Routing tab's rule list doesn't have a reorder handle. If a user expects ordering, surface a one-time tip on the rule editor: *"Rules don't have priority — all matching rules fire. Use overlapping trigger predicates if you want to encode 'low-priority shouldn't fire when high-priority does'."*

### "No transitive cascade" — what artefacts can re-enter the dispatcher

Per the design doc: artefacts created by a `RoutingRule` action (a tag from `EMIT_TAG`, a `ReviewItem` from `ESCALATE_TO_HUMAN_SCORER`) **do not** re-enter the lifecycle dispatcher. Scores produced by an escalated `Review` *do* fire `SCORE_VALUE` rules (because they're a real producing event).

UI implication: on the rule editor for `TAG_APPLIED` triggers, surface a helper distinguishing the two: *"Triggers on tags applied by users in the UI or by `SESSION_ENDED`-driven system events. Does **not** trigger on tags emitted by other routing rules — those don't cascade."* This prevents a class of bug-report where someone expects an emit-tag rule's output to fire another tag-applied rule.

### Lifecycle event types — quick reference table for the designer

| Event | Fires when | Available as routing trigger | Drives continuous scoring? |
|---|---|---|---|
| `SESSION_ENDED` | Session reaches terminal state | yes | yes (v1) |
| `AUTOMATED_RUN_FINISHED` | `AssessmentRun` reaches `COMPLETED` | yes | no |
| `USER_FEEDBACK_RECEIVED` | A `Score(source=USER_FEEDBACK)` is written | yes | no (but `Source.filter_query_string` can read these scores) |
| `TAG_APPLIED` | `CustomTaggedItem` created (human or system) | yes | no |

`TRACE_FINISHED` does **not** exist in v1 (D-15). UI must not suggest it; per-message scoring runs at session end.

## Open design questions

1. **Filter editor — free-text vs visual builder.** Drafted as both (free-text primary, visual chips as a progressive disclosure). The visual builder might be too complex for v1; deferring may be the right call. Decision pending.
2. **`NOTIFY` channel set.** The brief assumes existing notification primitives are reusable. If they're insufficient (e.g. no Slack/webhook for assessment-specific channels), specify what minimum surface is needed — defer to a separate, smaller brief if so.
3. **Cross-Assessment `ADD_TO_QUEUE` discoverability.** Listed in S8b's preset chips but flagged as fire-and-forget; some users will reach for it expecting concordance. Should it be hidden behind a "show advanced actions" toggle? Worth user-testing.
4. **Per-rule "dry-run last 24h" preview cost.** Useful but potentially expensive to compute on every editor open. If query cost is high, gate behind an explicit button rather than rendering on load.
5. **Visual representation of `AppliedRoutingRule.context` JSON.** Drafted as "key-value chips"; if the JSON shape varies wildly by rule kind, may need per-trigger-kind formatters. Designer's call.

## Cross-references

| Topic | Where |
|---|---|
| Filter language to reuse (not the FilterSet model) | [D-12](../unified-assessment.md#d-12-reuse-the-filter-language-not-the-filterset-model) |
| Why continuous Assessments don't have Runs | [D-5](../unified-assessment.md#d-5-continuous-assessments-do-not-produce-assessmentrun-rows) |
| Score targets and message-granularity = Trace | [D-13](../unified-assessment.md#d-13-score-targets-are-measurement-units-trace-experimentsession-evaluationmessage-not-display-surfaces) |
| `AppliedRoutingRule` audit shape | [D-14](../unified-assessment.md#d-14-audit-row-generalises-across-all-routing-rule-action-types) |
| Lifecycle hooks, idempotency, `AppliedSourceFilter` | [D-15](../unified-assessment.md#d-15-lifecycle-hooks-dispatch-in-parallel-to-statictrigger-with-consumer-side-idempotency) |
| `HUMAN_DISAGREEMENT` + authoritative Reviews | [D-16](../unified-assessment.md#d-16-reviewer-disagreement-is-resolved-by-authoritative-reviews-not-by-statistical-fiat) |
| Cross-Assessment routing constraints (no concordance back) | [FR-11](../unified-assessment.md#fr-11-external-escalation-cross-assessment-routing-backlog-5) |
| Today's `EvaluatorTagRule` (the shape being generalised) | [`apps/evaluations/models.py:555`](../../../apps/evaluations/models.py) |
| Filter parser support for sessions + traces | [`apps/filters/`](../../../apps/filters/) |
