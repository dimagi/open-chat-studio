# Unified Assessment System

> Single canonical document covering context, user stories, requirements, and design for OCS's unified assessment system. This consolidates and supersedes the earlier separate `unified-assessment-requirements.md`, `unified-assessment-design.md`, and `ocs-assessments-stories.md` documents. Where prior decisions conflict, this document takes precedence.

## TL;DR

We propose **two layers of consolidation** plus one piece of net-new infrastructure:

1. **Top-down: one `Assessment` is the user-facing unit of configuration.** It replaces `EvaluationConfig` and `AnnotationQueue` as separate concepts. An Assessment owns a source, a schema, one or more *scorers* (automated, human, or both), and a set of routing rules.
2. **Bottom-up: one `Score` is the system's unit of value.** Both automated runs and human reviews write Score rows. Concordance, aggregation, and trends all read from this single table.
3. **Net-new: a routing-rule abstraction with a rich trigger surface.** Generalises today's `EvaluatorTagRule` to support triggers on score values, lifecycle events, human flags, and tag applications, with action types covering tag emission, escalation between scorers, and notifications.

The middle layer (runs, reviews, results, automated-result rows) stays specialised. The user never sees those layers; the system uses different shapes for them because the underlying acts differ (Celery batch with retries vs reviewer submission with drafts and flags).

## Context

OCS has two assessment subsystems today:

- [`apps/evaluations/`](../../apps/evaluations/) — automated scoring via LLM judges or Python code, batch-orchestrated through Celery.
- [`apps/human_annotations/`](../../apps/human_annotations/) — multi-reviewer rubric annotation, with queues, assignees, and flag/draft workflow.

Both are in alpha/beta. They share more than they diverge:

- The schema language is identical: both use `FieldDefinition` from [`apps/evaluations/field_definitions.py`](../../apps/evaluations/field_definitions.py).
- The aggregation math is shared: `apps/human_annotations/aggregation.py` imports `aggregate_field` from `apps/evaluations/aggregation.py`.
- They produce structurally identical results: `{field_name: value}` dicts.
- They both attach to `ExperimentSession` (via `Chat` for tags; via `AnnotationItem` for queues).

What's *missing* is the connective tissue. The #1 backlog item — concordance analysis — currently requires exporting from both systems, matching by session ID externally, and analysing in a separate tool. The user-experience consequence is that workflows like "online evals with human review follow-up" require configuring five or six places that don't know about each other.

**Current state of the backlog (May 2026).** Six of the nine 2026 backlog items have been delivered as point solutions in the existing evals + annotations subsystems: auto-population (#2), session-mode datasets (#3), evaluator tag rules (#4), session ID in exports (#7), and bidirectional dataset ↔ queue imports (#8, #9). See the [Backlog item mapping](#backlog-item-mapping) table for code references. What remains structural is the *connective tissue*: shared schema, a single value layer, in-system concordance, and a routing-rule surface that subsumes today's `EvaluatorTagRule` and the one-shot import buttons. The unified design re-pitches those point solutions as parallel paths that should be consolidated, not features that don't exist.

## User stories

The system must support these workflows. Stories are grouped by lifecycle stage; concordance and multi-reviewer workflows cut across both stages.

### Development-time assessments

**Story 1 — Offline LLM-judge assessment to verify quality**
> As a **Bot Builder**, I want to run my chatbot over a curated test dataset and have LLM judges score each output, so that I can verify the quality of my changes before deploying.

**Story 2 — Manual calibration of LLM judges**
> As a **Bot Builder**, I want to manually assess a sample of items that an LLM judge has scored, so that I can measure whether the judge is a reliable proxy for human judgment before I trust it.

**Story 3 — Regression checks across versions**
> As a **Bot Builder**, I want to compare LLM-judge scores between a new chatbot version and a baseline version on the same dataset, so that I can catch regressions before deploying.

### Production-time assessments

**Story 4 — Continuous LLM-judge monitoring on production**
> As a **Bot Owner**, I want LLM judges to automatically assess a sample of production conversations on an ongoing basis, so that I can monitor real-world quality without manual review of every interaction.

**Story 5 — Human review queue, judge-flagged**
> As a **Reviewer**, I want to see a queue of production conversations that LLM judges flagged as low-quality or concerning, so that I can manually validate or correct the judge and triage real issues.

**Story 6 — Human review queue, user-feedback-flagged**
> As a **Reviewer**, I want to see a queue of production conversations where users gave negative feedback, so that I can investigate and learn from real failures.

### Cross-cutting analysis

**Story 7 — Concordance between humans and judges**
> As a **Bot Builder** or **Team Lead**, I want to see agreement metrics between human and judge scores on the same items, so that I can trust (or distrust) my automated evaluation pipeline and improve it.

**Story 8 — Trend monitoring across AssessmentResults and over time**
> As a **Bot Owner** or **Team Lead**, I want a dashboard showing assessment trends across multiple AssessmentResults and over time on production, so that I can track quality progression and catch issues early.

### Multi-reviewer workflows

**Story 9 — Inter-rater reliability**
> As a **Team Lead**, I want a configurable portion of review work to be assigned to multiple reviewers in parallel, so that I can measure inter-rater agreement and trust that human scores are well-calibrated.

**Story 10 — Second-pass review for uncertain items**
> As a **Reviewer**, I want to flag items I'm uncertain about for a second-pass review by another reviewer, so that ambiguous cases get appropriate attention rather than being decided by a single fallible judgment.

## Functional requirements

What the system must do, with priority levels and pointers into existing code.

### FR-1: Schema definition

A schema defines the structured fields that scorers (automated or human) produce. Both evaluators and annotation queues must use the same schema format.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1.1 | Define schemas as named, reusable objects owned by a team | Must |
| FR-1.2 | Support field types: string, int, float, choice | Must |
| FR-1.3 | Support validation constraints per field type (min/max, pattern, choices) | Must |
| FR-1.4 | Support `required` flag per field | Must |
| FR-1.5 | A schema is reusable across multiple Assessments and is shared by all scorers within an Assessment (both automated and human, with each scorer addressing a subset via `output_fields` — see D-10) | Must |
| FR-1.6 | Schemas are logically immutable once `Score` rows reference them; edits create a new schema row and repoint the Assessment (clone-and-repoint, not in-place edit). See D-8. | Must |

**Existing code:**
- `FieldDefinition` union type: [`apps/evaluations/field_definitions.py`](../../apps/evaluations/field_definitions.py)
- Schema validation in annotation queue form: [`apps/human_annotations/forms.py:clean_schema`](../../apps/human_annotations/forms.py)
- Dynamic form generation from schema: [`apps/human_annotations/forms.py:build_annotation_form`](../../apps/human_annotations/forms.py)
- Schema-to-Pydantic conversion for LLM structured output: [`apps/evaluations/utils.py:schema_to_pydantic_model`](../../apps/evaluations/utils.py)

### FR-2: Session selection & data sources

Sessions are the primary unit being assessed. The system needs flexible ways to select which sessions to assess and what data to present to scorers.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-2.1 | Select sessions for assessment by manual selection (bulk picker) | Must |
| FR-2.2 | Select sessions by filter criteria (date range, tags, experiment, participant) | Must |
| FR-2.3 | **Auto-add new sessions** matching criteria as they arrive (backlog #2) | Should |
| FR-2.4 | **Import entire sessions** into assessment, not just individual messages (backlog #3) | Must |
| FR-2.5 | Support CSV import as a data source (with column mapping, history parsing) | Must |
| FR-2.6 | Support manual message entry (input/output pairs with optional context) | Should |
| FR-2.7 | Deduplicate sessions across assessment configs (prevent double-assessment) | Should |
| FR-2.8 | Track the source of each item (which session, which messages, how added) | Must |

**Existing code:**
- Session selection table: [`apps/human_annotations/views/queue_views.py:AnnotationQueueSessionsTableView`](../../apps/human_annotations/views/queue_views.py)
- Dataset creation from sessions: [`apps/evaluations/tasks.py:create_dataset_from_sessions_task`](../../apps/evaluations/tasks.py)
- CSV import with column mapping: [`apps/evaluations/tasks.py:create_dataset_from_csv_task`](../../apps/evaluations/tasks.py)
- Session clone logic: [`apps/evaluations/utils.py:make_evaluation_messages_from_sessions`](../../apps/evaluations/utils.py)
- `ExperimentSession` model: [`apps/experiments/models.py:1412`](../../apps/experiments/models.py)
- **Auto-population of datasets** (FR-2.3, backlog #2): [`apps/evaluations/models.py:DatasetAutoPopulationRule`](../../apps/evaluations/models.py) + periodic task [`apps/evaluations/auto_population.py:auto_populate_eval_datasets`](../../apps/evaluations/auto_population.py). Per-dataset rule with `source_experiment` + `filter_query_string`; auto-disables after 3 consecutive failures; can trigger delta runs via `auto_run_on_append`.
- **Session-mode datasets** (FR-2.4, backlog #3): [`apps/evaluations/models.py:EvaluationMessage.create_from_sessions`](../../apps/evaluations/models.py) — one `EvaluationMessage` per session, full conversation in `history`. Selection UI: [`apps/evaluations/views/dataset_views.py`](../../apps/evaluations/views/dataset_views.py).

### FR-3: Automated scoring

Automated scorers run code or LLMs against session data to produce structured results. This is batch-async work.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-3.1 | Support LLM-based evaluators with configurable prompt and structured output | Must |
| FR-3.2 | Support Python code evaluators with sandboxed execution | Must |
| FR-3.3 | Evaluator output must conform to the assessment schema | Must |
| FR-3.4 | Run evaluators in parallel across items (Celery chord pattern) | Must |
| FR-3.5 | Support preview runs on a sample subset (currently 10 items) | Should |
| FR-3.6 | Optionally generate bot responses via an experiment version before evaluating | Should |
| FR-3.7 | Support per-`AssessmentRun` bot version selection (specific, latest working, latest published). Version is a run-level parameter, not a config field (see D-3) | Should |
| FR-3.8 | **Emit categorical tags on sessions based on evaluation results** for use in filtering elsewhere — expressed as `RoutingRule(action=EMIT_TAG)` (backlog #4, see D-9 for tag semantics) | Must |
| FR-3.9 | Track run lifecycle: pending → processing → completed/failed | Must |
| FR-3.10 | Clean up temporary evaluation sessions after a TTL (currently 30 days) | Should |

**Existing code:**
- `LlmEvaluator`, `PythonEvaluator`: [`apps/evaluations/evaluators.py`](../../apps/evaluations/evaluators.py)
- Celery chord orchestration: [`apps/evaluations/tasks.py:run_evaluation_task`](../../apps/evaluations/tasks.py)
- Bot generation: [`apps/evaluations/tasks.py:run_bot_generation`](../../apps/evaluations/tasks.py)
- Version resolution: [`apps/evaluations/models.py:EvaluationConfig.get_generation_experiment_version`](../../apps/evaluations/models.py)
- **Tag emission from eval results** (FR-3.8, backlog #4): [`apps/evaluations/models.py:EvaluatorTagRule`](../../apps/evaluations/models.py) (rule shape) + [`apps/evaluations/tagging.py`](../../apps/evaluations/tagging.py) (matcher) + [`apps/evaluations/models.py:AppliedTag`](../../apps/evaluations/models.py) (audit row). Session-mode rules tag `session.chat`; message-mode rules tag `ChatMessage`. Generalised by `RoutingRule` / `AppliedRoutingRule` in the unified design — see D-14.

### FR-4: Human scoring

Human scorers (reviewers) annotate sessions one-at-a-time in the browser, producing structured results per the assessment schema.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-4.1 | Present items to reviewers one at a time with a form generated from the schema | Must |
| FR-4.2 | Support configurable base review count per item (`num_reviews_required`, 1–10) for consensus, plus a separate `irr_sample_rate` that adds one extra reviewer to a sampled fraction of items for inter-rater reliability (see D-11) | Must |
| FR-4.3 | Assign reviewers to queues (restrict who can annotate) | Must |
| FR-4.4 | **Assign specific sessions to specific reviewers** (backlog #6) | Should |
| FR-4.5 | Track item status: pending → in progress → completed / flagged | Must |
| FR-4.6 | Reviewers can flag items with a reason (append-only flag list) | Must |
| FR-4.7 | Reviewers can unflag items (resets to pending for re-evaluation) | Should |
| FR-4.8 | Skip items already reviewed by the current user | Must |
| FR-4.9 | Support draft annotations (save without submitting) | Could |
| FR-4.10 | **Auto-add sessions to queue based on eval tags** (backlog #5) | Should |
| FR-4.11 | Detect reviewer disagreement on completed items: categorical non-unanimous, or numeric stdev above a configurable threshold. Fires the `HUMAN_DISAGREEMENT` routing trigger; intended pairing is a `RoutingRule` escalating to an adjudicator. See D-16. | Should |
| FR-4.12 | Support **authoritative** Reviews — a Review flagged `is_authoritative=True` whose Scores override per-source consensus on the same `(target, name)`. Set by routing-rule action or by an admin override. See D-16. | Should |

**Existing code:**
- Annotation workflow: [`apps/human_annotations/views/annotate_views.py`](../../apps/human_annotations/views/annotate_views.py)
- Dynamic form builder: [`apps/human_annotations/forms.py:build_annotation_form`](../../apps/human_annotations/forms.py)
- Queue visibility (assignees): [`apps/human_annotations/models.py:AnnotationQueueManager.visible_to`](../../apps/human_annotations/models.py)
- Permission groups: [`apps/teams/backends.py:229-237`](../../apps/teams/backends.py)

### FR-5: Results storage & querying

Results are the core shared artifact. Both automated and human scoring produce results in the same format.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-5.1 | Store typed scores with: target reference, source enum, schema-field reference, value (numeric or categorical), timestamp. Each multi-field result is decomposed into one `Score` row per field — not stored as a data dict (see D-1) | Must |
| FR-5.2 | Tag each `Score` with its source enum: `LLM_JUDGE`, `PROGRAMMATIC`, `HUMAN_REVIEW`, `USER_FEEDBACK`, `SYSTEM` | Must |
| FR-5.3 | Link each `Score` to a measurement-unit target via GenericForeignKey: `Trace` (per-interaction), `ExperimentSession` (per-conversation), or `EvaluationMessage` (per-dataset-item) (see D-13) | Must |
| FR-5.4 | Support querying scores by: target, scorer, schema, source enum, date range, assessment | Must |
| FR-5.5 | **Include global session_id in all exports** (backlog #7) | Must |
| FR-5.6 | Support CSV and JSONL export of results | Must |
| FR-5.7 | Support CSV upload to correct/override results in **batch** `AssessmentRun`s (with re-aggregation of `AssessmentRunAggregate`). Continuous Assessments don't support upload-correction — feedback corrections write new `Score` rows (see D-5, D-8) | Should |
| FR-5.8 | Results are immutable after submission (corrections create new records or use explicit override flow) | Should |

**Existing code:**
- `EvaluationResult`: [`apps/evaluations/models.py`](../../apps/evaluations/models.py)
- `Annotation`: [`apps/human_annotations/models.py`](../../apps/human_annotations/models.py)
- CSV export (evals): [`apps/evaluations/views/evaluation_config_views.py:download_evaluation_run_csv`](../../apps/evaluations/views/evaluation_config_views.py)
- CSV export (annotations): [`apps/human_annotations/views/queue_views.py:ExportAnnotations`](../../apps/human_annotations/views/queue_views.py)
- **Global session ID in eval exports** (FR-5.5, backlog #7): `session` and `source_session` columns (= `ExperimentSession.external_id`) in [`apps/evaluations/const.py:EVALUATION_RUN_FIXED_HEADERS`](../../apps/evaluations/const.py), populated in `EvaluationRun.get_table_data`.

### FR-6: Aggregation & trends

Aggregation computes statistical summaries across results. Both systems already use identical aggregation logic.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-6.1 | Compute numeric aggregates: mean, median, min, max, stdev | Must |
| FR-6.2 | Compute categorical aggregates: mode, distribution (percentage per category) | Must |
| FR-6.3 | Exclude string fields from aggregation — `String` field type is never aggregated; all `Numeric`, `Choice`, and `Boolean` fields are always aggregated | Must |
| FR-6.5 | For batch runs: recompute `AssessmentRunAggregate` when new results land. For continuous Assessments: no stored aggregates — queries compute on demand from `Score` rows over a time window (see D-5) | Must |
| FR-6.6 | Support trend analysis across multiple runs/time periods | Should |
| FR-6.7 | Aggregates should be filterable by source type (show automated-only, human-only, or combined) | Should |
| FR-6.8 | Per-source human consensus respects authoritative overrides: if any Score on `(target, name)` is from an authoritative Review, it *is* the consensus for that field; otherwise compute mean/mode normally. See D-16. | Should |

**Existing code:**
- Aggregator framework: [`apps/evaluations/aggregators.py`](../../apps/evaluations/aggregators.py)
- Eval aggregation: [`apps/evaluations/aggregation.py:compute_aggregates_for_run`](../../apps/evaluations/aggregation.py)
- Annotation aggregation: [`apps/human_annotations/aggregation.py:compute_aggregates_for_queue`](../../apps/human_annotations/aggregation.py)
- Trend data builder: [`apps/evaluations/utils.py:build_trend_data`](../../apps/evaluations/utils.py)

### FR-7: Concordance analysis (backlog #1)

Top-priority cross-system feature. Compare automated vs human scores on the same sessions to measure evaluator reliability.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-7.1 | Match sessions that have both automated and human results | Must |
| FR-7.2 | Display side-by-side comparison of automated vs human scores per session | Must |
| FR-7.3 | Compute agreement metrics across matched sessions (per-field) | Must |
| FR-7.4 | For numeric fields: compute correlation, mean absolute error, bias | Should |
| FR-7.5 | For categorical fields: compute Cohen's kappa, percent agreement, confusion matrix | Should |
| FR-7.6 | Highlight sessions with highest disagreement for review | Should |
| FR-7.7 | Export concordance report as CSV | Should |
| FR-7.8 | Filter concordance analysis by date range, experiment, or tag | Should |

**Existing code:** None — entirely new. **Prerequisite:** FR-5.3 (session-linked results) and FR-3.8 (session tagging).

### FR-8: Mixed-source assessment workflows (backlog #8, #9)

Originally framed as "data flowing bidirectionally between automated and human assessment workflows." In the unified design, these requirements collapse into "add another scorer to the same Assessment" — there is no system A and system B to import between. The requirements are kept in this framing because they remain visible to users (e.g. "I have a judge running, now I want to add human review on top"), but the implementation is internal to one Assessment.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-8.1 | **Add a `HumanScorer` to an automated-only Assessment** so flagged items become reviewable. Conditional escalation expressed as a `RoutingRule` (e.g. "score < threshold → escalate-to-human"). Replaces the "import eval-scored sessions into an annotation queue" workflow (backlog #8). | Must |
| FR-8.2 | **Add an `AutomatedScorer` to a human-only Assessment** to use submitted reviews as ground-truth comparison. Replaces the "import annotated sessions into an eval dataset" workflow (backlog #9). | Must |
| FR-8.3 | Preserve provenance: every `RoutingRule` firing recorded in `AppliedRoutingRule` (see D-14) — `triggered_by` points at the originating result, `outcome` points at the produced artifact. | Should |
| FR-8.4 | Support filtering by `Score` field values when populating queues — handled by `Source.filter_query_string` reading against `Score` rows. | Should |

**Existing code (one-shot imports today; the unified design subsumes both into in-Assessment scorer composition):**
- **Dataset → annotation queue** (backlog #8): [`apps/human_annotations/views/queue_views.py:ImportFromDataset`](../../apps/human_annotations/views/queue_views.py) + [`apps/human_annotations/forms.py:ImportFromDatasetForm`](../../apps/human_annotations/forms.py). Pulls distinct sessions from a completed eval dataset, creates `AnnotationItem`s (`item_type=SESSION`), dedupes against existing queue.
- **Annotation queue → eval dataset** (backlog #9): [`apps/evaluations/forms.py:ImportFromAnnotationQueueForm`](../../apps/evaluations/forms.py) + dataset-view handler in [`apps/evaluations/views/dataset_views.py`](../../apps/evaluations/views/dataset_views.py); extraction via `create_dataset_from_sessions_task`. Only accepts queues whose items are `AnnotationItemType.SESSION`; produces a session-mode dataset.
- **Limitation today:** both flows are one-shot copies, not live composition. There is no schema sharing — the eval's `Evaluator.params["output_schema"]` and the queue's `AnnotationQueue.schema` remain independent JSON blobs that happen to use the same field names. Concordance still requires an external join.

### FR-11: External escalation (cross-Assessment routing, backlog #5)

A separate concern from FR-8: routing items into a *different* Assessment's `HumanScorer` (e.g. a safety team's generic high-stakes queue). Distinguished from within-Assessment escalation because the receiving Assessment has its own schema, scorers, and analytical scope — the originating signal is *consumed* but not *correlated*.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-11.1 | **Cross-Assessment `ADD_TO_QUEUE`** routes items from one Assessment's source to another Assessment's `HumanScorer`, expressed as `RoutingRule(action=ADD_TO_QUEUE)` with the receiving Assessment's HumanScorer in `action_config`. | Should |
| FR-11.2 | Cross-Assessment routing is **fire-and-forget**: produced Scores carry the receiving Assessment's `assessment` FK, not the originator's. There is **no concordance back to the originating Assessment** — within-Assessment join is the only concordance shape supported (FR-7). | Must |
| FR-11.3 | Provenance back to the originating signal lives only on `AppliedRoutingRule`: `triggered_by` points at the originating result, `outcome` points at the new `ReviewItem` in the receiving Assessment. | Should |

**Future-friendly note.** If two Assessments share the same `AssessmentSchema` (D-10 already supports this), cross-Assessment concordance becomes a join on shared schema field names — additive to add later if real demand surfaces. v1 scope: external escalation is consumption-only.

### FR-9: Session tagging (backlog #4)

| ID | Requirement | Priority |
|----|------------|----------|
| FR-9.1 | Apply categorical tags to sessions/messages based on assessment results — categorical labels (no value), per D-9. Typed value signals are stored as `Score` rows, not tags | Must |
| FR-9.2 | Tags are queryable for session filtering across the platform | Must |
| FR-9.3 | Automated scorers can auto-tag based on result values and configurable rules — expressed as `RoutingRule(action=EMIT_TAG)` | Must |
| FR-9.4 | Each emitted tag has provenance via `AppliedRoutingRule` (which rule fired, which result triggered it, when) — see D-14 | Should |
| FR-9.5 | Tags are visible on the session detail page | Should |
| FR-9.6 | Support tag-based triggers for downstream actions (e.g., add to annotation queue) — expressed as `RoutingRule(trigger=TAG_APPLIED)` | Should |

**Existing code:** Tag / `CustomTaggedItem` / `TaggedModelMixin` ([`apps/annotations/`](../../apps/annotations/models.py)) attach to `Chat` (1:1 with `ExperimentSession`) and `ChatMessage`. The Tag model already distinguishes human / system / version via `is_system_tag` + `category`. See D-9 for which tag uses migrate elsewhere in the unified design.

### FR-10: Access control & permissions

| ID | Requirement | Priority |
|----|------------|----------|
| FR-10.1 | Team admins can create/edit/delete assessment configs, schemas, evaluators | Must |
| FR-10.2 | Team admins can create/manage annotation queues and assign reviewers | Must |
| FR-10.3 | Reviewers can only see queues they're assigned to | Must |
| FR-10.4 | Reviewers can submit annotations and flag items, but not edit configs | Must |
| FR-10.5 | Anyone on the team can view results and aggregates | Should |
| FR-10.6 | Feature-gated behind a single team-managed Waffle flag (e.g. `ASSESSMENTS`), replacing today's separate flags for evaluations and human annotations | Must |

**Existing code:**
- Feature flag: [`apps/teams/flags.py:57`](../../apps/teams/flags.py) — `HUMAN_ANNOTATIONS`
- Permission groups: [`apps/teams/backends.py:229-237`](../../apps/teams/backends.py) — `ANNOTATION_REVIEWER_GROUP`
- Queue visibility: [`apps/human_annotations/models.py:AnnotationQueueManager.visible_to`](../../apps/human_annotations/models.py)

## Non-functional requirements

| ID | Requirement | Notes |
|----|------------|-------|
| NFR-1 | Team-scoped — all models inherit `BaseTeamModel` ([`apps/teams/models.py:138`](../../apps/teams/models.py)) | Existing pattern |
| NFR-2 | Automated runs must not block the web process — use Celery for batch work | Existing pattern via chord |
| NFR-3 | Human annotation must be synchronous — no Celery for the review workflow | Existing pattern |
| NFR-4 | Export support for CSV and JSONL | Both systems already support this |
| NFR-5 | All JSON fields use `SanitizedJSONField` (null byte / control char safety) | Existing pattern |
| NFR-6 | Temporary evaluation sessions cleaned up after TTL | Existing: 30-day TTL in [`apps/evaluations/tasks.py`](../../apps/evaluations/tasks.py) |
| NFR-7 | Support team cloning (all assessment configs, schemas, datasets) | Evals already cloned in [`apps/teams/management/commands/clone_team.py`](../../apps/teams/management/commands/clone_team.py); annotations not yet |

## Backlog item mapping

Where each 2026 backlog item lands, and what state it's in today. "Done" items work in the current evals + annotations subsystems as point solutions; the unified design subsumes them into shared shapes (single schema, single value layer, single rule surface) — see the noted limitation per row for what the unified design adds beyond the existing implementation.

| # | Backlog item | Requirement(s) | Design expression | Current state |
|---|-------------|----------------|-------------------|---------------|
| 1 | LLM eval vs human annotation concordance | FR-7 (all) | Built-in tab on any Assessment with ≥2 scorer types; query over `Score` rows | **Not done.** Highest priority. Requires the unified `Score` layer + shared schema to avoid external join-by-string-match. |
| 2 | Auto-add new sessions to eval | FR-2.3 | `Source.filter_query_string` set → continuous Assessment | **Done as point solution** via [`DatasetAutoPopulationRule`](../../apps/evaluations/models.py) + periodic task. Limitation: per-dataset rule, not generalised to human queues; produces dataset items rather than continuously streaming Scores. Carries forward as the prototype for `Source.filter_query_string`. |
| 3 | Import entire sessions into evals | FR-2.4 | `Source.granularity = session` | **Done as point solution** via session-mode `EvaluationDataset`s. Carries forward unchanged — `Source.granularity = session` is just renaming the existing mode. |
| 4 | Evals tag sessions for filtering | FR-9 (all) | `RoutingRule(action=EMIT_TAG)`; uses existing Tag infrastructure | **Done as point solution** via [`EvaluatorTagRule`](../../apps/evaluations/models.py) + [`tagging.py`](../../apps/evaluations/tagging.py) + [`AppliedTag`](../../apps/evaluations/models.py). Limitation: rule shape is eval-result-only (no lifecycle/flag/tag triggers; no escalate/notify/add-to-queue actions). Generalised to `RoutingRule` / `AppliedRoutingRule` (D-14). |
| 5 | Auto-add sessions to annotation queue from eval tags | FR-4.10, FR-9.6 (within-Assessment); FR-11 (cross-Assessment) | `RoutingRule(trigger=SCORE_VALUE, action=ESCALATE_TO_HUMAN_SCORER)` within one Assessment is the common case; cross-Assessment `ADD_TO_QUEUE` (FR-11) is the fire-and-forget escalation case | **Not done.** Requires the `RoutingRule` action surface (`EvaluatorTagRule` today emits tags only, doesn't add items to queues). |
| 6 | Assign specific sessions to specific reviewers | FR-4.4 | Property of `HumanScorer` | **Not done.** Today's `AnnotationQueue.assignees` is queue-wide; per-item assignment is new. |
| 7 | Export CSV with global session_id | FR-5.5 | Trivial in unified export path; targets carry session reference | **Done in evals**: `session` and `source_session` columns ([`EVALUATION_RUN_FIXED_HEADERS`](../../apps/evaluations/const.py)). Annotation queue exports should be audited for the same — track as a small carry-forward. |
| 8 | Import evals dataset into annotation queue | FR-8.1 | Add a `HumanScorer` to an automated-only Assessment | **Done as point solution** via [`ImportFromDataset`](../../apps/human_annotations/views/queue_views.py). Limitation: one-shot copy, no shared schema, no live composition. Unified design replaces the import button with "add a `HumanScorer` to the Assessment." |
| 9 | Import annotation items into evals dataset | FR-8.2 | Add an `AutomatedScorer` to a human-only Assessment | **Done as point solution** via [`ImportFromAnnotationQueueForm`](../../apps/evaluations/forms.py). Same limitation as #8 (one-shot, no shared schema). Unified design replaces with "add an `AutomatedScorer` to the Assessment." |

**Net remaining backlog work (informs sequencing — out of scope for this doc):** items 1, 5, 6 are net-new and depend on unified infrastructure (Score layer, RoutingRule, per-item assignment). Items 2, 3, 4, 7, 8, 9 are carry-forward: their behaviour is preserved by the unified design but their data shapes get unified (schema, value layer, rule surface, audit row).

## Design principles

1. **The user's unit of configuration is the Assessment, not the subsystem.** "I want to assess this signal" is one thing to set up, regardless of who or what does the scoring.
2. **The system's unit of value is the Score.** Automated and human paths write the same rows. Concordance, aggregation, and trends all join on one table.
3. **Specialised middle layers are fine.** A Celery batch run and a human-review submission are different acts; they should have different shapes. Forcing them into one table creates a sparse, hard-to-reason-about record.
4. **Reuse existing OCS patterns.** GenericForeignKey for polymorphic targets ([`apps/annotations/UserComment`](../../apps/annotations/models.py), [`apps/events/EventLog`](../../apps/events/models.py)), `BaseTeamModel` for team scoping, `archived_at` / `is_archived` for immutable-ish configs, `VersionsMixin` only where versioning of user-edited entities is genuinely needed.
5. **Don't invent infrastructure that already exists.** Session tagging works today via `Chat.tags` (Chat is 1:1 with `ExperimentSession`). The Tag model already supports the human/system/condition distinction via `is_system_tag` and `category`. Reuse, don't replace.

## Domain model

The conceptual model:

```
                    AssessmentSchema
                        │ (FK, shared catalogue)
                        ▼
                   Assessment ─── Source (dataset + optional live filter)
                        │
                        ├── Scorers (1..N, mixed types)
                        │   ├── AutomatedScorer (LLM-judge or Python)
                        │   └── HumanScorer    (queue: assignees, num_reviews, etc.)
                        │
                        └── RoutingRules (0..N)
                                trigger:  score-value | lifecycle | flag | disagreement | tag
                                action:   emit-tag | escalate | notify | add-to-queue

       ┌────────── runtime layer (specialised, system-internal) ────────┐
       │                                                                │
       │   AssessmentRun (batch)        ReviewItem (per-item-per-       │
       │     ├── AutomatedResult          HumanScorer)                  │
       │     │     (per-item, per-scorer,    └── Review (per-reviewer)  │
       │     │     workflow shell)                                      │
       │     │                                                          │
       │     └── writes Score rows         writes Score rows ──────────┐│
       │                                                               ││
       └───────────────────────────────────────────────────────────────┘│
                                                                        │
                                Score (the value layer)  ◀──────────────┘
                                  ├── target (GenericFK)
                                  ├── source enum (incl. USER_FEEDBACK,
                                  │                free-floating allowed)
                                  ├── name + data_type (denormalised)
                                  └── value_numeric | value_string
```

## Data model

### Assessment

The user-facing configuration object. One Assessment expresses one signal being measured, regardless of who measures it.

| Field | Notes |
|---|---|
| `team` | `BaseTeamModel` |
| `name`, `description` | |
| `schema` | FK to `AssessmentSchema` |
| `source` | Sub-row hanging off Assessment (see below). |
| `archived_at` | Archive-instead-of-delete |

**Replaces:** `EvaluationConfig` ([`apps/evaluations/models.py`](../../apps/evaluations/models.py)) and `AnnotationQueue` ([`apps/human_annotations/models.py`](../../apps/human_annotations/models.py)) as user-facing concepts.

### AssessmentSchema

A reusable, named schema that all scorers in one or more Assessments share. Lifted from inline JSON in `Evaluator.params["output_schema"]` and `AnnotationQueue.schema` to a top-level catalogue.

| Field | Notes |
|---|---|
| `team` | `BaseTeamModel` |
| `name`, `description` | |
| `fields` | JSON: `dict[name, FieldDefinition]`. Reuses existing types: `String`, `Int`, `Float`, `Choice`. |
| `created_at` | Indexed; lets the per-Assessment schema-history chain be queried in order. |

**New table.** Existing inline schemas are migrated lazily — the first time a Score is written referencing an inline schema, an `AssessmentSchema` row is materialised and FK'd.

**No `archived_at`.** Schemas are append-only catalogue entries; old rows are implicitly retained as long as anything FKs to them. See D-8 for the clone-and-repoint evolution model.

### Source

Sub-row hanging off Assessment. There is one primitive — a dataset of items — with an optional live filter that controls how items arrive.

| Field | Notes |
|---|---|
| `dataset` | **Nullable** FK to the dataset of items being assessed (`EvaluationDataset` carried forward, or whatever the unified equivalent becomes). Set for batch (offline) Assessments where the dataset is the explicit collection. **Null for continuous** — there is no materialised dataset; items are the live targets matching the filter, dedup is enforced via the unique index on `Score`. |
| `granularity` | `session` or `message`. Determines target type: for batch, the dataset's `EvaluationMessage` shape; for continuous, the live object fed through the filter (`ExperimentSession` for `session`, `Trace` for `message`). |
| `filter_query_string` | Nullable text. When set, the source is continuous and items stream in via lifecycle hooks. Reuses the filter language from [`apps/filters/`](../../apps/filters/models.py) but stored directly on the source — see D-12 for why we don't FK to `FilterSet`. |
| `sample_rate` | Used when `filter_query_string` is set: optional sampling on top of the filter match. |
| `bot_generation_experiment` | Optional FK; when set, the source synthesises bot responses before scoring (today's `EvaluationConfig.experiment_version` pattern, moved to source-level). Only meaningful for batch Assessments — continuous Assessments score live interactions where responses already exist. |

The continuous-vs-on-demand distinction is **implicit** from `filter_query_string` presence:

- **No `filter_query_string`** → items are added at configuration time. Population mechanisms (CSV import, bulk session-pick, manual one-by-one selection) are UI affordances for filling the same underlying dataset, not separate source kinds. The Assessment runs on demand and produces `AssessmentRun` rows.
- **`filter_query_string` set** → sessions or traces stream in via lifecycle hooks. The Assessment is continuous and produces no `AssessmentRun` rows. No dataset is materialised; dedup against re-fires is unconditional via the `(assessment, target, name, source)` unique index on `Score` — v1 supports only `SCORE_ONCE_PER_TARGET` semantics.

### Scorers

Discriminated sub-rows; an Assessment has 1..N. Same Assessment can mix `AutomatedScorer` and `HumanScorer` rows.

**`AutomatedScorer`** — replaces `Evaluator`:

| Field | Notes |
|---|---|
| `assessment` | FK |
| `kind` | `LLM_JUDGE` or `PYTHON` |
| `params` | JSON (model, prompt, code, etc.) — same shape as today's `Evaluator.params` |
| `output_fields` | List of field names from the Assessment's schema that this scorer produces. Lets each judge focus on a subset (see D-10). |

**`HumanScorer`** — absorbs queue-level config from `AnnotationQueue`:

| Field | Notes |
|---|---|
| `assessment` | FK |
| `assignees` | M2M to `CustomUser` |
| `num_reviews_required` | Default review count per item. Everything-gets-N consensus workflows set this >1. |
| `irr_sample_rate` | Decimal 0.0–1.0 (default 0.0). Sets the fraction of items flagged at queue-entry for inter-rater reliability — flagged items require `num_reviews_required + 1` reviews to complete. Lets the same `HumanScorer` express both "everyone reviews everything" and "single-review with a 20% IRR sample." See D-11. |
| `show_prior_automated_scores` | Bool, default `False`. Calibration (Story 2) wants `True` (anchor against the judge); parallel multi-review (Story 9) doesn't care. See D-7. |
| `show_prior_human_scores` | Bool, default `False`. Independence between reviewers (Story 9, IRR) requires `False`; second-pass-on-flag (Story 10) is hardcoded to show the flagging reviewer's score regardless. See D-7. |
| `output_fields` | Optional list of field names from the Assessment's schema that this scorer reviews. Defaults to all fields. Same semantics as `AutomatedScorer.output_fields` (see D-10). |

### RoutingRule

The general "if X then Y" wiring. Generalises today's `EvaluatorTagRule` ([`apps/evaluations/models.py`](../../apps/evaluations/models.py)).

| Field | Notes |
|---|---|
| `assessment` | FK |
| `trigger_kind` | `SCORE_VALUE`, `LIFECYCLE_EVENT`, `HUMAN_FLAG`, `HUMAN_DISAGREEMENT`, `TAG_APPLIED` |
| `trigger_config` | JSON; shape varies by `trigger_kind` |
| `action_kind` | `EMIT_TAG`, `ESCALATE_TO_HUMAN_SCORER`, `NOTIFY`, `ADD_TO_QUEUE` (cross-Assessment) |
| `action_config` | JSON; shape varies by `action_kind` |
| `sample_policy` | `EVERY` \| `THRESHOLD` \| `RANDOM_N_PERCENT`. Lets the same routing-rule shape express both Story 5 (escalate everything below threshold) and Story 2 (sample 10% randomly for calibration). |

This is closer to a mini-rules engine than today's `EvaluatorTagRule`. The rich trigger surface unblocks Stories 5, 6, 9, 10 and the online-evals lifecycle hooks.

**Execution semantics (v1):**

- **All matching rules fire.** No priority field, no short-circuit. If two rules both match, both fire. Users encode "low-priority shouldn't fire when high-priority does" via overlapping trigger predicates (`0.3 ≤ score < 0.5`). Priority is non-breaking to add later.
- **No transitive cascade.** Artefacts produced by a `RoutingRule` action (a `CustomTaggedItem` from `EMIT_TAG`, a `ReviewItem` from `ADD_TO_QUEUE`/`ESCALATE_TO_HUMAN_SCORER`) **do not** re-enter the lifecycle dispatcher. The artefact is created with a `trigger_lifecycle_hook=False` flag. Forward chaining is a v2 thing — explicit cascades are easier to reason about than implicit ones, and none of the ten user stories needs them.
- **Score rows produced by an escalated `Review` *do* fire `SCORE_VALUE` rules.** That's the natural consequence of a new score existing, not a cascade hop — escalation produces a `ReviewItem`, the reviewer's submission produces a `Review`, the `Review` saving produces Scores; each step is a real producing event.
- **`sample_policy` rolls deterministically per `(rule_id, triggered_by_id)`.** Hash those two together; same trigger, same rule, same outcome. Idempotent and re-fireable without skew. The roll outcome is recorded in `AppliedRoutingRule.context`.
- **Database-level backstop:** `AppliedRoutingRule.unique(rule, triggered_by, outcome)` (D-14) prevents a rule from double-recording a firing.

**Concrete combinations** the rule machinery is designed to express:

| Trigger | Action | Use case |
|---|---|---|
| `SCORE_VALUE` | `EMIT_TAG` | Judge tags low-quality sessions for downstream filtering. Today's `EvaluatorTagRule` workflow (backlog #4). |
| `SCORE_VALUE` | `ESCALATE_TO_HUMAN_SCORER` | Story 5 — items where automated score crosses a threshold are routed into the same Assessment's `HumanScorer` for review. |
| `SCORE_VALUE` | `NOTIFY` | Alert when a key signal (safety, compliance) drops below threshold in production. |
| `SCORE_VALUE` (with `RANDOM_N_PERCENT` sample policy) | `ESCALATE_TO_HUMAN_SCORER` | Story 2 — random calibration sample for human review of judge output. |
| `HUMAN_FLAG` | `ESCALATE_TO_HUMAN_SCORER` (different reviewer) | Story 10 — second-pass review for items a reviewer flagged as uncertain. |
| `HUMAN_DISAGREEMENT` | `ESCALATE_TO_HUMAN_SCORER` (adjudicator, mark authoritative) | Reviewers split on a categorical or stdev-out-of-range on a numeric field; adjudicator's call becomes the authoritative answer. See D-16. |
| `LIFECYCLE_EVENT` (session ended / run finished) | `ADD_TO_QUEUE` | Cross-Assessment routing — feed items from one Assessment's source into another's queue (backlog #5 when within-Assessment escalation isn't enough). |
| `TAG_APPLIED` | `NOTIFY` | Alert on emergency tag applied during conversation. |
| `TAG_APPLIED` | `ADD_TO_QUEUE` | Auto-populate an annotation queue from any tag — including human-applied UI tags, not just judge-emitted ones. |

### Score (the value layer)

The single table both automated runs and human reviews write to.

| Field | Notes |
|---|---|
| `team` | `BaseTeamModel`, denormalised for query scope |
| `target` | `GenericForeignKey` — one of `Trace` (per-interaction), `ExperimentSession` (per-conversation), or `EvaluationMessage` (per-dataset-item, offline). See D-13. |
| `name` | Schema field name; denormalised |
| `data_type` | `NUMERIC` \| `CATEGORICAL` \| `BOOLEAN`; denormalised |
| `value_numeric` | Nullable |
| `value_string` | Nullable |
| `score_config` | FK to `AssessmentSchema`, nullable (free-form scores allowed only for `USER_FEEDBACK`) |
| `source` | `LLM_JUDGE` \| `PROGRAMMATIC` \| `HUMAN_REVIEW` \| `USER_FEEDBACK` \| `SYSTEM` |
| `assessment` | Nullable FK; set when the Score originates inside an Assessment workflow |
| `assessment_run` | Nullable FK; set for batch automated scores |
| `automated_result` | Nullable FK to the `AutomatedResult` that produced this Score. One `AutomatedResult` spawns N Score rows (one per scorer `output_field`); this FK lets a Score navigate back to the raw judge output, error string, and generated bot response. |
| `review` | Nullable FK to the `Review` that produced this Score. Symmetric to `automated_result` on the human side. Free-floating Scores (e.g. `USER_FEEDBACK`) have both null. |
| `author` | Nullable FK to `CustomUser`; set for `HUMAN_REVIEW`. CHECK: exactly one of (`author`, `participant`) populated for human/feedback sources; both null for automated. |
| `participant` | Nullable FK to `Participant`; set for `USER_FEEDBACK` (channel users aren't `CustomUser`s). See D-2 for why two FKs + CHECK rather than GFK. |
| `comment` | Inline rationale text |
| `created_at` | Indexed; primary axis for trend queries |

**Unique constraints.**

- `(automated_result_id, name)` — artefact-level idempotency for automated. Re-running an `AutomatedResult` overwrites by deleting+recreating its Scores.
- `(review_id, name)` — artefact-level idempotency for human. Resubmission of a `Review` overwrites.
- `(assessment_id, target_content_type, target_object_id, name, source)` — semantic idempotency for continuous mode. The same Assessment cannot score the same target with the same field-and-source twice (see D-5 / Source). This is the production-correctness backstop for the lifecycle dispatch idempotency story (D-15).
- `USER_FEEDBACK` Scores have no producing artefact FK; producer-side dedup on `(target, source=USER_FEEDBACK, author OR participant, name)` — a participant's most recent thumb on a Trace overwrites their previous one. Enforced via partial unique index or in the ingress code.

**Free-floating Scores.** `assessment` and `assessment_run` are nullable so that user-feedback Scores can be written into the table independent of any Assessment. Every user-feedback ingress writes a Score row with `source=USER_FEEDBACK`. Assessments can then filter on these (the Source's filter reads from Score) to drive Story 6's "user-feedback-flagged review queue" workflow.

**System-reserved field names.** Free-floating Scores have no schema, so their `name` comes from a system-reserved registry — today just `user_thumb` (`data_type=NUMERIC`, `value_numeric ∈ {-1, +1}`). No `AssessmentSchema` may declare a field with a reserved name. Future feedback axes (free-text reasons, structured "what was wrong" choices) reserve additional names (`user_feedback_reason`, etc.) without schema-FK plumbing.

**Concordance is not a raw join on Score rows.** With ensembles (multiple judges scoring the same field) and multi-reviewer queues (`num_reviews_required > 1`), `(target, name, source)` can have many rows. Concordance pre-aggregates one or both sides into per-source consensus Scores (mean for numeric, mode for categorical, with N) before joining. Story 9's inter-rater reliability skips the human-side pre-aggregation and groups by `Score.author` instead.

### Runtime layer (specialised, system-internal)

These models stay close to their existing shapes; only their role in the architecture changes.

- **`AssessmentRun`** — replaces `EvaluationRun`. Exists only for batch executions. Carries `bot_version` (moved from configuration to runtime — see D-3), `status`, `finished_at`, `job_id`, `error_message`. Continuous Assessments produce no `AssessmentRun` rows.
- **`AutomatedResult`** — replaces `EvaluationResult`. Per-item-per-`AutomatedScorer`-per-run shell. Holds the raw output JSON, error string, generated bot response, etc. Each row spawns N Score rows on save (one per output schema field).
- **`ReviewItem`** — replaces `AnnotationItem`. Per-item-per-`HumanScorer` work unit. Carries status (`pending` / `in_progress` / `completed` / `flagged`), flag history, review count, and an `is_irr_sample` bool set at item creation if the IRR sample roll succeeded (drives the `+1` completion threshold; see D-11).
- **`Review`** — replaces `Annotation`. Per-reviewer submission. Holds the `data` JSON dict, status (`draft` / `submitted`), and **`is_authoritative: bool`** (default `False`). On submission, spawns N Score rows. Authoritative Reviews override per-source consensus on the same `(target, name)` during aggregation. See D-16.
- **`AppliedRoutingRule`** — generalises today's `AppliedTag`. Audit row recording every `RoutingRule` firing. See D-14.
- **`AppliedSourceFilter`** — audit row for continuous-dispatch **failures and skips only** (filter-no-match, dedup, sample-rolled-out, scorer-error). See the [Lifecycle hooks](#operational-audit-appliedsourcefilter-failures-only) section.

The user does not see these. They are workflow shells.

## Lifecycle hooks

Continuous Assessments (those with `Source.filter_query_string` set) and `RoutingRule(trigger=LIFECYCLE_EVENT)` both depend on a small, well-defined set of system events. This section describes those events, how they dispatch, and how they relate to OCS's existing events infrastructure ([`apps/events/`](../../apps/events/)).

### The four event types

| Event | Fires when | Payload (IDs only) |
|---|---|---|
| `SESSION_ENDED` | An `ExperimentSession` transitions to a terminal state (overlaps with today's `CONVERSATION_END_*` `StaticTriggerType`s) | `session_id` |
| `AUTOMATED_RUN_FINISHED` | An `AssessmentRun` reaches `COMPLETED` (with or without errors). Lets one Assessment's output drive another's input. | `assessment_run_id` |
| `USER_FEEDBACK_RECEIVED` | A `Score` row is written with `source=USER_FEEDBACK`. Drives Story-6 user-feedback queues. | `score_id` |
| `TAG_APPLIED` | A `CustomTaggedItem` is created — system-emitted or human-applied, both fire the same hook. Drives `RoutingRule(trigger=TAG_APPLIED)`. | `tagged_item_id` |

The same four events serve two consumers:

- **Continuous Assessment sources.** When an event fires, the system enumerates active live-filter Assessments whose granularity matches the event payload, applies each Assessment's `Source.filter_query_string` against the referenced object, and writes a Score on the matching target (subject to the `(assessment, target, name, source)` unique index — `SCORE_ONCE_PER_TARGET` semantics). For continuous mode, **only `SESSION_ENDED` is the v1 dispatch event for assessment scoring** — see "v1 scope" below.
- **`RoutingRule(trigger=LIFECYCLE_EVENT)`.** Same dispatch surface; the rule's `trigger_config` declares which event type it subscribes to, and the matching item flows through whatever action the rule declares. All four event types are available to routing rules.

### v1 scope: continuous assessment dispatch is `SESSION_ENDED` only

A continuous Assessment with `Source.granularity = message` targets `Trace`s — but there is no `TRACE_FINISHED` event in v1. Per-message continuous scoring runs at session end by walking the session's traces. This means continuous monitoring has session-end latency (sessions can be hours long), not per-trace latency. Acceptable for the current set of stories — none requires sub-session-end scoring latency.

`TRACE_FINISHED` is a v2 addition when a real-time-alerting use case shows up. The architecture supports adding it without restructuring (parallel-to-`StaticTrigger` pattern, dispatch fan-out scales the same way).

The other three events (`AUTOMATED_RUN_FINISHED`, `USER_FEEDBACK_RECEIVED`, `TAG_APPLIED`) serve `RoutingRule` triggers and `USER_FEEDBACK_RECEIVED`-driven Source filters; they don't directly drive continuous scoring against live targets.

### Dispatch model

Same pattern as today's events infrastructure ([`apps/events/tasks.py`](../../apps/events/tasks.py)) — Celery task per event, payload is object IDs only:

1. Code that produces the event (e.g. `session.end()`, `AssessmentRun.mark_completed()`, `Score.objects.create(source=USER_FEEDBACK, ...)`, `CustomTaggedItem.objects.create(...)`) calls `enqueue_assessment_lifecycle_event(event_type, object_id)`.
2. That task queries active subscribers — continuous Assessments matching the event type and granularity, plus `RoutingRule`s with a matching `LIFECYCLE_EVENT` trigger.
3. For each subscriber, enqueues `dispatch_lifecycle_event_to_subscriber(subscriber_kind, subscriber_id, event_type, object_id)`.
4. Each per-subscriber task re-fetches the object, evaluates the filter or rule, performs the action, and writes an `AppliedRoutingRule` row (for routing rules) or extends the dataset (for sources).

Latency isn't a constraint; per-event fan-out cost is acceptable.

### Relationship to `StaticTrigger`

[`StaticTrigger`](../../apps/events/models.py) overlaps with `SESSION_ENDED` and tag-related events, but its `experiment` FK ties each trigger to one `Experiment` version. Assessments aren't experiment-scoped, so `StaticTrigger` is the wrong shape to absorb Assessment lifecycle dispatch.

The split:

- **`StaticTrigger` unchanged** — continues to handle per-Experiment, conversation-centric triggers (`CONVERSATION_END_*`, `NEW_HUMAN_MESSAGE`, etc.) wired to `EventAction` side effects (send-message, end-conversation, run-pipeline).
- **Assessment lifecycle dispatch is parallel** — its own Celery task names, its own subscriber lookup, no shared data with `StaticTrigger`. Architecture (Celery + IDs + EventLog-style audit) is reused; rows aren't.
- **Where they overlap** (a session end should fire both), the producing call site (`session.end()`) invokes both dispatchers. Cheap and explicit.

If a future generalisation can fold both into one dispatcher, that's an additive refactor — for v1, treat them as parallel systems with shared DNA.

### Idempotency

The dispatch surface itself does **not** guarantee at-most-once delivery. Celery may retry a task, and a legitimate re-fire is possible (e.g. a session ends, gets resumed, ends again). The system tolerates this because **every consumer effect is idempotent**, leaning on uniqueness constraints that already exist for other reasons:

- **Tag emission** — `CustomTaggedItem`'s unique constraint on `(content_type, object_id, tag)` makes re-application a no-op.
- **Routing rule firing** — `AppliedRoutingRule`'s unique constraint on `(rule, triggered_by, outcome)` (D-14) records a given firing once.
- **Continuous scoring** — `Score`'s unique index on `(assessment, target_content_type, target_object_id, name, source)` is the production-correctness backstop for `SCORE_ONCE_PER_TARGET` semantics. Re-firing an event for an already-scored target is a no-op.
- **Batch scoring** — automated scores within a run are scoped to `(automated_result, name)`; re-evaluating in the same run is overwrite, not duplicate.

Same pattern as today's `StaticTrigger.fire()`: dispatch is dumb, consumers carry the correctness contract. This avoids a parallel dispatch-ledger table that would just duplicate state these constraints already track.

### Operational audit: `AppliedSourceFilter` (failures only)

Continuous dispatch needs an answer to "why didn't *this* session get scored?" without paying the cost of one audit row per matched event. The compromise: an `AppliedSourceFilter` table that records **failures and skips only**, never successes (those are visible from the produced `Score` row).

| Field | Notes |
|---|---|
| `assessment` | FK to the Assessment whose Source was evaluated |
| `event_type` | One of the four lifecycle event types |
| `target` | `GenericForeignKey` — the object the filter was evaluated against |
| `outcome` | `FILTER_NO_MATCH` \| `DEDUP_SKIP` \| `SAMPLE_ROLLED_OUT` \| `SCORER_ERROR` |
| `error_message` | Populated for `SCORER_ERROR` only |
| `created_at` | Indexed |

Operational health for a continuous Assessment then becomes three queries: latest `Score.created_at`, count of Scores in the last 24h grouped by source, count of `AppliedSourceFilter` failures in the last 24h. No new "health" state model.

Successes are *not* audited: with N continuous Assessments × M session-ends/day this is the dominant write cost; collapsing it to "produced Score row exists" recovers the budget. The same shape can be reused for `RoutingRule` lifecycle-trigger dispatch failures if needed; for v1, scope it to source-filter dispatch.

### No backfill

Lifecycle hooks fire forward only. A newly-created continuous Assessment does **not** retroactively pick up historical sessions, runs, feedback, or tags. If backfill is ever required, it's a separate operation — a one-shot management command that walks historical data and presents it to a specific Assessment, not part of the hook surface.

## How the user stories map to the unified design

| # | Story | Configuration in the unified model |
|---|---|---|
| 1 | Offline LLM-judge over a curated test set | `Assessment(source.dataset=…, scorers=[automated_llm_judge])` (no `filter_query_string`; dataset populated at config time) |
| 2 | Manual calibration of an LLM judge | Same as Story 1 plus `scorers += [human]`; `routing = [random-sample-N% → human]`; `human.show_prior_automated_scores = True`, `show_prior_human_scores = False` |
| 3 | Regression checks across versions | One Assessment, two `AssessmentRun`s with different `bot_version`. Compare via the Runs tab. |
| 4 | Continuous LLM-judge monitoring | `Assessment(source.filter_query_string=…, scorers=[automated_llm_judge])` (no dataset materialised) with `sample_rate` on the source |
| 5 | Human queue, judge-flagged | `Assessment(source.filter_query_string=…, scorers=[automated, human])`; `routing = [score < threshold → escalate-to-human]` |
| 6 | Human queue, user-feedback-flagged | `Assessment(source.filter_query_string=[USER_FEEDBACK Score < 0], scorers=[human])`. Relies on user feedback writing free-floating Scores. |
| 7 | Concordance between humans and judges | Built-in tab on any Assessment with ≥2 scorer types. Pre-aggregate Scores per `(target, name, source)` into consensus values (mean for numeric, mode for categorical), then join on `(target, name)` across sources. |
| 8 | Trend monitoring | Two distinct views: "Runs" tab (batch comparison) and "Trends" tab (continuous time-windowed query). Both read from Score; neither uses a unified abstraction. |
| 9 | Inter-rater reliability | `human.irr_sample_rate > 0` for the sampled-IRR pattern, or `human.num_reviews_required > 1` for everything-gets-N. Both with `human.show_prior_human_scores = False`. Skip human-side pre-aggregation; group by `Score.author` instead of `Score.source`. |
| 10 | Second-pass review for uncertain items | `routing = [trigger=HUMAN_FLAG → action=ESCALATE_TO_HUMAN_SCORER(different reviewer)]`. Escalation flow always shows the flagging reviewer's score on the second-pass review. A parallel pattern handles system-detected disagreement: `routing = [trigger=HUMAN_DISAGREEMENT → action=ESCALATE_TO_HUMAN_SCORER(adjudicator, mark_authoritative=True)]` — see D-16. |

All ten stories fit. Five of them surfaced refinements that are baked into the model above (D-3, D-4, D-7, D-10, D-11).

## Key design decisions

### D-1: Two-layer consolidation, not one
- **Decision:** Unify at the top (`Assessment`) and at the bottom (`Score`). Keep specialised models in the middle.
- **Rejected alternative:** Single `AssessmentResult` table for both automated and human results.
- **Why — bounded vs unbounded sparseness.** `Score` is itself sparse-by-source (`automated_result` null for human, `review` null for automated, `value_numeric` and `value_string` alternate per `data_type`), so sparseness *per se* isn't the rejection criterion. The principle is **bounded vs unbounded** sparseness:
  - `Score`'s null pattern is along a small, fixed, *enum-keyed* axis. Given `source`, the populated columns are known. Adding a new source variant is a deliberate schema decision; the table doesn't accrete columns when a scorer kind is added.
  - A unified `Result` table would carry the union of `{error_message, retry_count, generated_bot_response, draft_status, flag_history, submitted_at, reviewer_id, queue_position, ...}`. Each new scorer-kind or workflow variant adds columns. The sparseness is *open-ended*; the table accumulates dead columns over time.
- **What belongs where.** Bounded, value-shaped data that all sources can populate (e.g. `comment`, the inline rationale) lives on `Score`. Workflow-shell artefacts (`error_message`, retry count, draft status, flag history, generated bot response) live on the runtime models (`AutomatedResult`, `Review`, `ReviewItem`) — they describe the *act of running*, not the value produced.

### D-2: GenericForeignKey for polymorphic targets
- **Decision:** `Score`, like `UserComment` and `CustomTaggedItem`, uses `GenericForeignKey` for its target.
- **Rejected alternative:** Multiple nullable FKs + CHECK constraint.
- **Why:** GFK is the established OCS pattern for cross-cutting concerns ([`apps/annotations/UserComment`](../../apps/annotations/models.py), [`apps/events/EventLog`](../../apps/events/models.py)). The CHECK-constraint pattern would be the *only* such pattern in the codebase. Consistency wins.
- **One deliberate exception.** `Score`'s author axis uses two nullable FKs + CHECK (`author → CustomUser` and `participant → Participant`, exactly one populated for human/feedback sources, both null for automated). The variants are exhaustively known and never grow — a CHECK is cheaper than GFK overhead when the axis is fixed and binary.

### D-3: `bot_version` lives on `AssessmentRun`, not on the Assessment
- **Decision:** When you run an Assessment, you pick (or default) the bot version per run.
- **Rejected alternative:** Keep version on the configuration row, as today's `EvaluationConfig.experiment_version`.
- **Why:** Story 3 (regression checks across versions) only works cleanly if "run the same Assessment against v3 then v4" is a runtime choice. Today's pattern requires duplicating the configuration to compare versions, which fragments the comparison.

### D-4: User feedback writes free-floating `Score` rows
- **Decision:** Every user-feedback ingress writes a `Score` row with `source=USER_FEEDBACK`, no `assessment` FK required. Feedback uses **system-reserved field names** (e.g. `user_thumb`) rather than schema-FK plumbing — see the Score data-model section.
- **Rejected alternative:** User feedback is a separate signal, ingested only through Assessment-specific filters.
- **Why:** Treats feedback as a first-class typed signal in the same store as automated and human scores. Story 6's user-feedback queue becomes a filter expression over Score rather than a special-purpose pathway. Cost: every feedback ingress point writes a Score — mechanical but disciplined.
- **Concordance with feedback is a feature, not a shape guarantee.** An LLM judge produces `{accuracy, helpfulness}`; feedback produces `{user_thumb}`. They aren't on the same axis and can't be naively field-by-field compared. Story 7's "do users agree with the judge?" requires a per-Assessment configured comparison (e.g. "treat `user_thumb=1` as agreement when `judge.overall_quality > 0.5`"). The Score model carries the data; whether two arbitrary signals can be compared is a per-view decision, not a model invariant.
- **Future expansion.** Today's feedback is just `user_thumb`. Future axes (free-text reasons, "what went wrong" choices) reserve additional names (`user_feedback_reason`, etc.); each is a Score row of the appropriate `data_type`. A multi-axis 👎 (`-1` plus reason `"hallucinated"`) writes two Scores.

### D-5: Continuous Assessments do not produce `AssessmentRun` rows
- **Decision:** A live-filter Assessment streams Scores; there is no batch-run row to attach them to. Aggregates for continuous Assessments are time-windowed queries over Score.
- **Rejected alternative:** Synthesise one `AssessmentRun` per time window (e.g., daily).
- **Why:** Inventing run rows is accounting fiction. Cleaner: `AssessmentRun` exists only when there's an actual batch act of running (status, started_at, finished_at). For continuous, the unit of analysis is the time window, computed on demand.

### D-6: Separate "Runs" and "Trends" views; shared Score plumbing only
- **Decision:** Batch run trends (Story 3) and continuous trends (Story 8) live in distinct dashboard views. Both read from `Score`; neither uses a unified "trend abstraction."
- **Rejected alternative:** One unified time-axis chart that overlays batch runs as points and continuous as a band.
- **Why:** They answer different *questions*. "Did v4 beat v3?" needs paired comparison and bot_version filtering. "Is production drifting?" needs a rolling window. Forcing them into one chart hurts both. Keep them separate; if a combined view is wanted later, add it without changing the model.

### D-7: Two prior-score visibility knobs on `HumanScorer`, not one
- **Decision:** Split visibility into `show_prior_automated_scores` and `show_prior_human_scores` (both bool, default `False`).
- **Rejected alternative:** A single `show_prior_scores` bool collapsing both axes.
- **Why:** Calibration (Story 2) wants prior *automated* scores visible (anchor against the judge) but prior human scores hidden (irrelevant noise — calibration is judge-vs-this-reviewer). IRR (Story 9) wants prior *human* scores hidden (independence between reviewers) but doesn't care about automated. A single bool conflates these and forces an Assessment running calibration alongside IRR sampling into a contradiction.

| Story | `show_prior_automated_scores` | `show_prior_human_scores` |
|---|---|---|
| 2 — Calibration | `True` | `False` |
| 9 — IRR / parallel multi-review | either | `False` |
| Default review queue | `False` | `False` |

Story 10's "second-pass reviewer sees the flagging reviewer's score" is **hardcoded** in the escalation flow (a flagged item routed to a different reviewer always shows the flagging reviewer's score and reason) rather than expressed as a per-routing-rule visibility override. If a real use case for hiding the prior score on second-pass review surfaces later, an override on `RoutingRule.action_config` is non-breaking to add.

### D-8: AssessmentSchema is a real catalogue, not embedded JSON
- **Decision:** Promote `FieldDefinition` from inline JSON in `Evaluator.params` and `AnnotationQueue.schema` to a top-level `AssessmentSchema` row.
- **Why:** Concordance requires both sides to be talking about the same fields. Today, "the human queue's `helpfulness` field" and "the LLM judge's `helpfulness` field" are independent strings that happen to match. A shared catalogue makes concordance a join, not a fuzzy match. Lazy migration: existing inline schemas materialise into rows on first reference, FK gets set, then both sides share.

**Evolution: clone-and-repoint, not in-place edit.**
- Every schema change creates a *new* `AssessmentSchema` row. The Assessment's `schema` FK repoints; existing Scores keep their original `score_config` FK pointing at the old row. Old schema rows are implicitly retained as long as anything FKs to them; no `archived_at` needed.
- Even pure additions are clone-and-repoint. Adding a field in place creates a "v1 with extra field that nobody scores" partial state, since scorer `output_fields` reference the v1 row.
- **Trend continuity is a query-side concern, not a model-side one.** Dashboard aggregation joins on **field name**, ignoring `score_config` FK:
  ```sql
  SELECT date_trunc('day', created_at), source, AVG(value_numeric)
  FROM score
  WHERE assessment_id = ? AND name = 'accuracy'
  ```
  Additive evolution preserves trend continuity for unchanged fields automatically. New fields' trends start at the v2 introduction time.
- Two failure modes managed by UI affordances at edit time:
  - **Rename** (`accuracy` → `factuality`) breaks trend continuity for that field. UI warns; the user owns the consequence.
  - **Type change at the same name** (e.g. `Int 1–5` → `Float 0–1`) silently mixes incomparable values under a naive aggregation. UI **requires** picking a new name on type change, so historical and new Scores don't aggregate together.
- Schema history per Assessment is queryable as the chain of `AssessmentSchema` rows ordered by `created_at` — diagnostic, not the default view.

### D-9: Tagging stays for label-shaped uses; two existing uses migrate out
- **Decision:** Tags ([`apps/annotations/`](../../apps/annotations/models.py)) continue to handle **ad-hoc human tags applied via UI** and **system-applied condition tags** (e.g. "emergency"). The Tag / `CustomTaggedItem` / `TaggedModelMixin` infrastructure is unchanged for these uses, and continues to live on `Chat` (1:1 with `ExperimentSession`) and `ChatMessage`.
- **Two existing tag uses migrate out:**
  - **User feedback (👍/👎)** moves to `Score` rows with `source=USER_FEEDBACK` and `target=Trace`, per D-4 and D-13. Today's tag-based representation pre-dates the unified design; moving it into Score makes feedback a typed signal that participates in concordance ("do users agree with the judge?") and aggregation, and re-anchors it on `Trace` (the unit of interaction) rather than the display-surface `ChatMessage`.
  - **Bot version on messages** moves to a first-class field on `ChatMessage`: `experiment_version_number = PositiveIntegerField(null=True, blank=True)`, mirroring the existing `Trace.experiment_version_number` shape. Null = produced by the working version; integer = published version number. **No FK to `Experiment`** — the working-version reference is already reachable via `chat → session → experiment`; adding an FK to a specific version row would either misrepresent the working-version case (the working row evolves over time) or lose the pointer entirely for messages produced from working state. The split (parent-pointer + version_number) is what `Trace` does and is index-friendly for "all messages from v3 of experiment X" (`WHERE experiment_session.experiment_id = X AND experiment_version_number = 3`). `ExperimentSession.experiment_versions` already aggregates version at the session level; the message-level integer is what's missing.
- **Why:** Tags are the right primitive for "this categorical label applies" with no associated value. User feedback has a value (positive/negative, possibly numeric) — that's a Score. Bot version is a structural fact about every AI message — that's a column. Pushing both out of tags reduces tag-overload and makes each shape queryable on its own terms.
- **Rejected alternative:** Promote tagging to `ExperimentSession` directly, or build a new session-tags primitive. Not needed once feedback and version are removed; what's left is well-served by the existing model.

### D-10: Shared schema with per-scorer field subsets, not schema-per-scorer
- **Decision:** The Assessment owns one `AssessmentSchema`. Each scorer declares an `output_fields` subset of that schema — the fields it actually produces. Multiple judges, each with focused prompts, are supported by giving each a different (possibly disjoint, possibly overlapping) subset.
- **Rejected alternative:** Schema-per-scorer, as today's `Evaluator.params["output_schema"]`.
- **Why:** Today's pattern allows judges to evolve independently but makes concordance a fuzzy match by field name and gives no top-level "what does this Assessment measure" view. Shared-schema-with-subsetting keeps the focused-prompt benefit (each judge's prompt is templated against its `output_fields`, not the union), gives concordance a real join key (field identity in the catalogue, not string match), and exposes a coherent Assessment-level summary. Multiple judges covering the *same* field is allowed and natural — it's how ensemble scoring or judge-vs-judge concordance is expressed.
- **Migration:** existing per-`Evaluator` schemas are unioned into one `AssessmentSchema` per Assessment; each `Evaluator`'s old schema becomes its `output_fields` subset.

### D-11: IRR sampling is a separate field, sampled at queue-entry
- **Decision:** `HumanScorer.irr_sample_rate` (Decimal) controls a fraction of items flagged at queue-entry for inter-rater reliability. Flagged items require `num_reviews_required + 1` reviews; unflagged items use `num_reviews_required`. The flag (`is_irr_sample`) is stored on the `ReviewItem` at creation, not computed at review time.
- **Rejected alternative:** Always require `num_reviews_required` for every item. Forces a binary "everyone reviews everything or no one does," which makes IRR prohibitively expensive at scale — the only way to measure agreement is to triple your review cost.
- **Rejected alternative:** Decide IRR-sampling at review-time (when reviewer N+1 is about to mark an item complete). Adds randomness to completion logic and makes "which items are IRR-sampled?" un-queryable retroactively.
- **Why:** IRR is a sampled measurement, not a property of the workflow. `num_reviews_required` describes what the workflow needs; `irr_sample_rate` describes what we'll measure to validate it. Keeping them separate lets the same `HumanScorer` express both single-review-with-IRR-sample and full-consensus-with-IRR-sample without introducing a new "review mode" enum.
- **Note on adjudication:** IRR sampling is for *measuring agreement*, not *resolving disagreement*. When reviewers actually disagree and a canonical answer is needed, the mechanism is `HUMAN_DISAGREEMENT` + authoritative Reviews (see D-16), independent of IRR sampling.
- **Scope of v1:** IRR sampling adds exactly one extra reviewer when triggered. If "+N extra reviewers" becomes a real need, an `irr_extra_reviews` field is non-breaking to add later.

### D-12: Reuse the filter language, not the `FilterSet` model
- **Decision:** `Source.filter_query_string` stores the filter expression directly. The parsing/application logic from [`apps/filters/`](../../apps/filters/models.py) is reused; the `FilterSet` model is not.
- **Rejected alternative:** `Source.filter_set → FilterSet` FK.
- **Why:** `FilterSet` is per-user with sharing/default flags and a UI-table `table_type` discriminator (SESSIONS, ANNOTATION_ITEMS, etc.). Those properties make sense for a user-saved UI filter; they do not fit a filter that conceptually belongs to an Assessment. Forcing `FilterSet` into this role would mean adding an `ASSESSMENT_SOURCE` table_type that doesn't correspond to a real UI table, plus carrying user/sharing flags that don't apply.
- **Ergonomics for users:** offer a UI affordance that copies a saved `FilterSet`'s `filter_query_string` into a Source as a starting point. The Source then owns its own filter and can diverge.
- **Parser coverage confirmed:** the existing parser supports both `SESSIONS` and `TRACES` table types, which covers the two Score-target granularities Assessments will use (`ExperimentSession` per D-13 session-level, `Trace` per D-13 interaction-level). No parser adaptation needed for v1.

### D-13: Score targets are measurement units (Trace, ExperimentSession, EvaluationMessage), not display surfaces
- **Decision:** `Score.target` is one of three concrete types: `Trace` (per-interaction; live-assessment and user-feedback granularity), `ExperimentSession` (per-conversation; live-assessment session-level granularity), or `EvaluationMessage` (per-dataset-item; offline-assessment granularity). `Chat` and `ChatMessage` are explicitly **not** Score targets.
- **Why:** `Trace` records the unit of measurable interaction (one LLM call / pipeline execution) with full context — prompt, response, error info, participant data snapshot, duration. `ChatMessage` is the display surface — just text + role. Scoring a display surface conflates "what was said" with "what happened to produce it"; scoring the trace gives an unambiguous referent. Same reasoning for `Chat` vs `ExperimentSession`: `ExperimentSession` is the conversational unit with experiment, participant, state; `Chat` is the message container, 1:1 with the session.
- **User feedback target migration:** today's 👍/👎 attaches to `ChatMessage` via tags. With this decision, new feedback writes `Score(target=Trace, source=USER_FEEDBACK)`. Historical feedback is **dropped** during the migration — no backfill onto Trace, no legacy retention on `ChatMessage`. The cost is acceptable given alpha/beta status; the simpler migration is worth more than preserving early feedback signal.
- **Historical data without traces:** can't participate in per-interaction scoring (no trace to attach to). Still scorable at the session level via `ExperimentSession`, or by importing into a dataset as `EvaluationMessage`. Forward progress, not retroactive loss.
- **Tagging is unaffected.** Tags continue to attach to `Chat` and `ChatMessage` per D-9. Tag targets and Score targets are independent.

### D-14: Audit row generalises across all routing-rule action types
- **Decision:** `AppliedTag` (today's audit row tying a fired `EvaluatorTagRule` to a tagged item) is generalised into `AppliedRoutingRule` — a single audit table that records every `RoutingRule` firing, regardless of action type.
- **Shape:** `(rule FK, triggered_at, triggered_by GFK, outcome GFK, context JSON)`.
  - `triggered_by` points at whatever caused the rule to fire — `AutomatedResult`, `Review`, `ExperimentSession` (for `LIFECYCLE_EVENT` triggers), `CustomTaggedItem` (for `TAG_APPLIED` triggers), etc.
  - `outcome` points at whatever the action produced — `CustomTaggedItem` (for `EMIT_TAG`), `ReviewItem` (for `ESCALATE_TO_HUMAN_SCORER` and `ADD_TO_QUEUE`), notification record (for `NOTIFY`), etc.
  - `context` JSON captures rule-specific provenance: which score field matched, what value, whether the IRR sample roll succeeded, etc.
- **Why generalise:** with `RoutingRule` itself broadened across four trigger kinds and four action kinds (the rich trigger surface), keeping a tag-only audit table would mean inventing a parallel audit table per action kind. A single GFK-on-both-sides table matches the existing OCS pattern (`UserComment`, `EventLog`) and gives a uniform "what happened, why, and what came of it" log.
- **Unique constraint:** `(rule, triggered_by_content_type, triggered_by_object_id, outcome_content_type, outcome_object_id)` — same rule producing the same outcome from the same trigger doesn't double-record. The rule machinery should also short-circuit on idempotency before reaching the audit write; the constraint is a database-level backstop.

### D-15: Lifecycle hooks dispatch in parallel to `StaticTrigger`, with consumer-side idempotency
- **Decision:** Assessment lifecycle events (`SESSION_ENDED`, `AUTOMATED_RUN_FINISHED`, `USER_FEEDBACK_RECEIVED`, `TAG_APPLIED`) dispatch via their own Celery task path with their own subscriber registry. Architecture mirrors today's `apps/events/` (Celery task with object IDs only, fan-out to per-subscriber tasks); data does not. The dispatch surface does not guarantee at-most-once — Celery retries and legitimate re-fires are tolerated. Correctness is a consumer-side property: every action handler is idempotent, leaning on existing uniqueness constraints (`CustomTaggedItem`, `AppliedRoutingRule`) plus the new `Score(assessment, target, name, source)` unique index that serves as the production-correctness backstop for continuous scoring. No backfill — hooks fire forward only.
- **v1 dispatch scope:** for continuous **assessment scoring**, only `SESSION_ENDED` is the dispatch event. Per-message granularity scores at session end (no `TRACE_FINISHED` event in v1). All four event types are available to `RoutingRule(trigger=LIFECYCLE_EVENT)`.
- **Operational audit** is captured in `AppliedSourceFilter` for **failures and skips only** (filter-no-match, dedup, sample-rolled-out, scorer-error). Successes are visible from the produced `Score` row. See the [Lifecycle hooks](#lifecycle-hooks) section for the table shape.
- **Rejected alternative — extend `StaticTrigger`:** `StaticTrigger.experiment` ties each trigger to one `Experiment` version. Assessments aren't experiment-scoped, so shoehorning them in would force a per-Experiment shape (or a special-case "no experiment" trigger). Share the architectural pattern, not the data model.
- **Rejected alternative — dispatch-ledger table for at-most-once:** Would duplicate state that already exists in the action-side uniqueness constraints. Same pattern today's `StaticTrigger.fire()` follows: keep dispatch dumb, push correctness to consumers.
- **See:** the [Lifecycle hooks](#lifecycle-hooks) section for the event types, dispatch flow, idempotency mechanics, audit shape, and `StaticTrigger` relationship in full.

### D-16: Reviewer disagreement is resolved by authoritative Reviews, not by statistical fiat
- **Decision:** Reviewer disagreement is handled by two small additions: a new `HUMAN_DISAGREEMENT` routing trigger that fires when reviewers don't agree on a completed item, and an `is_authoritative: bool` flag on `Review` whose Scores override per-source consensus for the same `(target, name)`. The intended pairing is `RoutingRule(trigger=HUMAN_DISAGREEMENT, action=ESCALATE_TO_HUMAN_SCORER(adjudicator, mark_authoritative=True))`.
- **What "disagreement" means:**
  - **Categorical:** not unanimous.
  - **Numeric:** stdev across reviewers above a per-rule threshold (in `trigger_config`).
  - **String:** never triggers disagreement (strings don't aggregate; see FR-6.3).
- **Three workflows on one mechanism:**
  - **No adjudication needed** (today's implicit shape): `num_reviews_required = 1`, no routing rule. Each Review is the answer.
  - **Statistical consensus** (multi-reviewer averaging): `num_reviews_required > 1`, no routing rule. Mean/mode is the consensus; ties for categorical surface as "no consensus established" until adjudicated.
  - **Adjudicated consensus**: `num_reviews_required > 1` + `RoutingRule(trigger=HUMAN_DISAGREEMENT → adjudicate-authoritative)`. Most disagreements are caught and resolved by a designated adjudicator; result is a single canonical answer.
- **Aggregation rule (FR-6.8):** "if any Score on `(target, name)` is from an authoritative Review, it *is* the consensus for that field; otherwise compute mean/mode normally." One line, no special-casing in the query layer beyond a where-clause preference.
- **Setting `is_authoritative`:**
  - Procedurally by routing-rule action — `ESCALATE_TO_HUMAN_SCORER` accepts `mark_authoritative=True` in its `action_config`. The escalated Review is auto-flagged on submission.
  - Manually by a team-lead-permission user — explicit toggle in the UI on any Review.
- **Rejected alternative — promote authoritative to a separate `Score.source` enum value (`HUMAN_AUTHORITATIVE`):** conflates "who/what produced this" (a property of the source kind) with "is this the final answer" (a property of the act of submission). Cleaner to keep `source=HUMAN_REVIEW` for all human Scores and put the authoritative flag on the producing artefact (`Review`).
- **Rejected alternative — reviewer hierarchy / vote weighting:** introduces a ranking concept (senior reviewer, lead reviewer, etc.) that doesn't exist elsewhere in OCS. The authoritative flag captures the same outcome without adding a new dimension to the permission model. "Adjudicator" is just whoever the routing rule (or admin override) routes to.
- **Scope:** v1 supports binary `is_authoritative`. Partial-field authority (override only the disputed fields, leave others as consensus) is expressed by submitting a Review with only the disputed fields in `output_fields` — re-uses D-10's per-scorer subset mechanism rather than adding new authority granularity.

## Mapping to existing OCS code

| Today | Becomes |
|---|---|
| [`EvaluationConfig`](../../apps/evaluations/models.py) | `Assessment` |
| [`AnnotationQueue`](../../apps/human_annotations/models.py) | `Assessment` (with `HumanScorer` sub-rows for queue config) |
| [`Evaluator`](../../apps/evaluations/models.py) | `AutomatedScorer` |
| `Evaluator.params["output_schema"]` (inline JSON) | `AssessmentSchema` (FK), populated lazily |
| `AnnotationQueue.schema` (inline JSON) | `AssessmentSchema` (FK), populated lazily |
| [`EvaluationDataset`](../../apps/evaluations/models.py) | Carried forward as the `dataset` FK on `Source` |
| [`EvaluationMessage`](../../apps/evaluations/models.py) | unchanged; still the dataset item type |
| [`EvaluationRun`](../../apps/evaluations/models.py) | `AssessmentRun` (batch only) |
| [`EvaluationResult`](../../apps/evaluations/models.py) | `AutomatedResult` (workflow shell) + N `Score` rows (value layer) |
| [`AnnotationItem`](../../apps/human_annotations/models.py) | `ReviewItem` |
| [`Annotation`](../../apps/human_annotations/models.py) | `Review` (workflow shell) + N `Score` rows |
| [`EvaluatorTagRule`](../../apps/evaluations/models.py) | `RoutingRule` (broader trigger surface) |
| [`AppliedTag`](../../apps/evaluations/models.py) | Generalised into `AppliedRoutingRule`: one audit table for all `RoutingRule` firings, regardless of action type. See D-14. |
| [`DatasetAutoPopulationRule`](../../apps/evaluations/models.py) | Subsumed into `Source.filter_query_string` semantics (continuous mode). The auto-population rule's `source_experiment` + `filter_query_string` + `is_enabled` become properties of the Assessment's Source; the periodic Celery scan becomes the lifecycle-hook dispatch (D-15). Auto-disable-after-N-failures behaviour carries forward as an operational concern on `AppliedSourceFilter` failure counts. |
| [`ImportFromDataset`](../../apps/human_annotations/views/queue_views.py) / [`ImportFromAnnotationQueueForm`](../../apps/evaluations/forms.py) | Retired as user-facing one-shot imports. Equivalent behaviour expressed as "add another scorer to the same Assessment" (FR-8). |
| [`EvaluationRunAggregate`](../../apps/evaluations/models.py) | unchanged — still the eager batch-aggregate cache |
| [`AnnotationQueueAggregate`](../../apps/human_annotations/models.py) | retired; aggregates queried from `Score` |
| [`Tag`](../../apps/annotations/models.py), [`CustomTaggedItem`](../../apps/annotations/models.py), [`UserComment`](../../apps/annotations/models.py) | Unchanged for ad-hoc human tags and system condition tags. See D-9 for the two tag uses that migrate out. |
| User-feedback tags (👍/👎 today) | `Score` rows with `source=USER_FEEDBACK`, target = `Trace`. Historical feedback dropped during migration. |
| Bot-version tags on `ChatMessage` (today) | `ChatMessage.experiment_version_number` (nullable int, mirrors `Trace.experiment_version_number`). Working-version pointer reached via `chat → session → experiment`. `ExperimentSession.experiment_versions` continues to aggregate at the session level. |
| Online-evals saved-filter (issue #3044) | `Source.filter_query_string` (reusing the filter language from [`apps/filters/`](../../apps/filters/models.py); see D-12) + `RoutingRule` (trigger=`LIFECYCLE_EVENT`) |

## Out of scope (for this document)

- **Sequencing and migration plan.** A separate document.
- **UI design.** This document fixes data-model and back-end shape only.
- **API contracts.** Will follow once the model is settled.
- **Cross-Assessment concordance.** All ten stories scope concordance to within-Assessment. Cross-Assessment can be added later as an ad-hoc report; no model accommodation needed.
- **Combined batch + continuous timeline view.** Decided against (D-6); revisit if real user demand emerges.
- **Migration of historical alpha data.** Depends on the answer to "is alpha data wipeable" (open question 1).
- **Materialised continuous-trend aggregates.** Decided to compute on-demand initially; add precomputed daily/weekly buckets only if performance demands it.
- **User comments.** [`UserComment`](../../apps/annotations/models.py) (free-form text notes attached polymorphically to `Chat`, `ChatMessage`, etc.) is unrelated to the score/review machinery and is not consolidated in this design. It continues to live in [`apps/annotations/`](../../apps/annotations/) unchanged.

## Open questions

These need answers before sequencing. None blocks the design.

1. **Alpha-data wipeability.** Are existing `EvaluationConfig`, `EvaluationRun`, `AnnotationQueue`, and `Annotation` rows safe to wipe and re-seed during the migration to `Assessment`, or must they be preserved? Wipeable simplifies the migration substantially; preservation is doable but adds work.
2. **Dogfood target.** Is there a known team or use case to validate the unified Assessment configuration UX against? "Story 5 — judge-flagged human queue" is the natural integration test for whether the unified configuration actually fixes the fragmentation pain.

## Glossary

| Term | Meaning |
|---|---|
| **Assessment** | The user-facing configuration object; the unit a user sets up to measure a signal. Replaces `EvaluationConfig` and `AnnotationQueue` as user-facing concepts. |
| **AssessmentSchema** | A reusable, named set of typed fields. Both automated and human scorers in an Assessment share one. |
| **Source** | The data-feeding configuration of an Assessment: a dataset of items (batch mode) *or* a live filter that streams in matching sessions/traces (continuous mode; no dataset materialised). |
| **Scorer** | Who or what produces scores in an Assessment. `AutomatedScorer` (LLM judge or Python) or `HumanScorer` (queue with assignees). An Assessment can have several. |
| **RoutingRule** | An "if X then Y" rule on an Assessment. Triggers on score values, lifecycle events, human flags, or tag applications. Actions include emitting tags, escalating between scorers, notifying, or adding to another Assessment's queue. |
| **AssessmentRun** | A batch execution of an Assessment. Continuous (live-filter) Assessments do not produce runs. |
| **AutomatedResult** | Per-item, per-`AutomatedScorer`, per-run workflow shell. Holds raw output and errors. Spawns Score rows on save. |
| **ReviewItem** | Per-item, per-`HumanScorer` work unit for human review. Replaces `AnnotationItem`. |
| **Review** | Per-reviewer submission against a `ReviewItem`. Replaces `Annotation`. Spawns Score rows on submission. May be flagged `is_authoritative=True` — see D-16. |
| **Authoritative Review** | A `Review` whose Scores override per-source consensus on the same `(target, name)` during aggregation. Set procedurally by a `HUMAN_DISAGREEMENT` routing rule's adjudication action, or manually by a team-lead-permission user. See D-16. |
| **Score** | A single typed score value attached to a target. Written by all sources (automated, human, user-feedback). The unit aggregation, concordance, and trends query against. |
| **AppliedRoutingRule** | Audit row recording a `RoutingRule` firing. Generalises today's `AppliedTag`. |
| **AppliedSourceFilter** | Audit row recording continuous-dispatch **failures and skips** (filter-no-match, dedup, sample-rolled-out, scorer-error). Successes are not audited — visible from the produced Score row. |
| **Concordance** | Cross-source agreement analysis on the same items, scoped within a single Assessment. Cohen's Kappa for categorical, correlation/MAE for numeric. Computed by **pre-aggregating per source** (consensus, mean for numeric, mode for categorical) and then joining on `(target, name)`; for inter-rater reliability the human side is grouped by `Score.author` instead of pre-aggregated. |
| **Aggregate** | Statistical summary across results — mean, mode, distribution, etc. Computed eagerly per `AssessmentRun` for batch; computed on-demand over time windows for continuous. |
