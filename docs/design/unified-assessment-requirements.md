# Unified Assessment System — Requirements

> Starting-from-scratch requirements for a system that unifies automated
> evaluations and human annotations into a single assessment framework.
> Incorporates all 2026 backlog items. References existing code for context.

## Problem Statement

OCS has two separate subsystems for assessing conversation quality:

- **Automated evaluations** (`apps/evaluations/`) — LLM or Python code scores
  messages from datasets, producing structured results per evaluator.
- **Human annotations** (`apps/human_annotations/`) — human reviewers annotate
  sessions/messages in queues, producing structured results per reviewer.

Both systems share the same schema format (`FieldDefinition`), the same
aggregation math (`aggregate_field`), and produce structurally identical results
(`{field_name: value}` dicts). But they can't talk to each other — the #1
backlog item (concordance analysis) currently requires exporting from both,
matching sessions externally, and analyzing in a separate tool.

The 2026 backlog has 4 of 9 items that are explicitly cross-system. This
document defines what a unified design would need to support.

## Glossary

| Term | Definition |
|------|-----------|
| **Assessment** | Any structured evaluation of a conversation, automated or human |
| **Schema** | A set of typed field definitions that describe what to score |
| **Session** | An `ExperimentSession` — the primary unit being assessed |
| **Scorer** | Who/what produces scores: an evaluator (automated) or a reviewer (human) |
| **Result** | A single set of structured scores for one item from one scorer |
| **Aggregate** | Statistical summary across results (mean, mode, distribution, etc.) |
| **Concordance** | Comparison of results from different scorers on the same sessions |

## Domain Model (Conceptual)

```
Schema ──defines fields for──▶ Assessment Config
                                    │
                        ┌───────────┴───────────┐
                        ▼                       ▼
              Automated Config            Human Config
              (evaluators, dataset,       (queue, assignees,
               experiment version)         review policy)
                        │                       │
                        ▼                       ▼
                  Automated Run            Human Review
              (Celery, parallel)        (browser, one-by-one)
                        │                       │
                        └───────────┬───────────┘
                                    ▼
                               Result Set
                          (per-item structured scores,
                           source-tagged)
                                    │
                                    ▼
                              Aggregation
                         (per-run or per-queue stats)
                                    │
                                    ▼
                             Concordance
                     (cross-scorer comparison on
                      same sessions)
```

## Functional Requirements

### FR-1: Schema Definition

A schema defines the structured fields that scorers (automated or human)
produce. Both evaluators and annotation queues must use the same schema
format.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-1.1 | Define schemas as named, reusable objects owned by a team | Must |
| FR-1.2 | Support field types: string, int, float, choice | Must |
| FR-1.3 | Support validation constraints per field type (min/max, pattern, choices) | Must |
| FR-1.4 | Support `required` and `use_in_aggregations` flags per field | Must |
| FR-1.5 | A schema can be shared across multiple assessment configs (both automated and human) | Must |
| FR-1.6 | Schema changes are blocked after results exist (or require explicit migration) | Must |

**Existing code:**
- `FieldDefinition` union type: [`apps/evaluations/field_definitions.py`](../../apps/evaluations/field_definitions.py)
- Schema validation in annotation queue form: [`apps/human_annotations/forms.py:clean_schema`](../../apps/human_annotations/forms.py)
- Dynamic form generation from schema: [`apps/human_annotations/forms.py:build_annotation_form`](../../apps/human_annotations/forms.py)
- Schema-to-Pydantic conversion for LLM structured output: [`apps/evaluations/utils.py:schema_to_pydantic_model`](../../apps/evaluations/utils.py)

---

### FR-2: Session Selection & Data Sources

Sessions are the primary unit being assessed. The system needs flexible ways
to select which sessions to assess and what data to present to scorers.

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
- `ExperimentSession` model: [`apps/experiments/models.py:1412`](../../apps/experiments/models.py) — fields: `external_id`, `experiment`, `participant`, `chat`, `state`, `platform`

---

### FR-3: Automated Scoring

Automated scorers (evaluators) run code or LLMs against session data to
produce structured results. This is batch-async work.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-3.1 | Support LLM-based evaluators with configurable prompt and structured output | Must |
| FR-3.2 | Support Python code evaluators with sandboxed execution | Must |
| FR-3.3 | Evaluator output must conform to the assessment schema | Must |
| FR-3.4 | Run evaluators in parallel across items (Celery chord pattern) | Must |
| FR-3.5 | Support preview runs on a sample subset (currently 10 items) | Should |
| FR-3.6 | Optionally generate bot responses via an experiment version before evaluating | Should |
| FR-3.7 | Support experiment version selection: specific, latest working, latest published | Should |
| FR-3.8 | **Tag sessions with evaluation results** for use in filtering elsewhere (backlog #4) | Must |
| FR-3.9 | Track run lifecycle: pending → processing → completed/failed | Must |
| FR-3.10 | Clean up temporary evaluation sessions after a TTL (currently 30 days) | Should |

**Existing code:**
- `LlmEvaluator`: [`apps/evaluations/evaluators.py`](../../apps/evaluations/evaluators.py) — uses `LlmService` for structured output with retries
- `PythonEvaluator`: [`apps/evaluations/evaluators.py`](../../apps/evaluations/evaluators.py) — uses `RestrictedPythonExecutionMixin` from [`apps/utils/python_execution.py`](../../apps/utils/python_execution.py)
- Celery chord orchestration: [`apps/evaluations/tasks.py:run_evaluation_task`](../../apps/evaluations/tasks.py)
- Bot generation: [`apps/evaluations/tasks.py:run_bot_generation`](../../apps/evaluations/tasks.py) — creates temp `ExperimentSession` via `ChannelPlatform.EVALUATIONS` ([`apps/channels/models.py:33`](../../apps/channels/models.py))
- Version resolution: [`apps/evaluations/models.py:EvaluationConfig.get_generation_experiment_version`](../../apps/evaluations/models.py)

---

### FR-4: Human Scoring

Human scorers (reviewers) annotate sessions one-at-a-time in the browser,
producing structured results per the assessment schema.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-4.1 | Present items to reviewers one at a time with a form generated from the schema | Must |
| FR-4.2 | Support configurable number of reviews per item (1–10) for consensus | Must |
| FR-4.3 | Assign reviewers to queues (restrict who can annotate) | Must |
| FR-4.4 | **Assign specific sessions to specific reviewers** (backlog #6) | Should |
| FR-4.5 | Track item status: pending → in progress → completed / flagged | Must |
| FR-4.6 | Reviewers can flag items with a reason (append-only flag list) | Must |
| FR-4.7 | Reviewers can unflag items (resets to pending for re-evaluation) | Should |
| FR-4.8 | Skip items already reviewed by the current user | Must |
| FR-4.9 | Support draft annotations (save without submitting) | Could |
| FR-4.10 | **Auto-add sessions to queue based on eval tags** (backlog #5) | Should |

**Existing code:**
- Annotation workflow: [`apps/human_annotations/views/annotate_views.py`](../../apps/human_annotations/views/annotate_views.py) — `AnnotateQueue`, `SubmitAnnotation`, `FlagItem`
- Dynamic form builder: [`apps/human_annotations/forms.py:build_annotation_form`](../../apps/human_annotations/forms.py)
- Queue visibility (assignees): [`apps/human_annotations/models.py:AnnotationQueueManager.visible_to`](../../apps/human_annotations/models.py)
- Review count + status update: [`apps/human_annotations/models.py:Annotation.save`](../../apps/human_annotations/models.py) — atomic increment + status recalc
- Permission groups: [`apps/teams/backends.py:229-237`](../../apps/teams/backends.py) — `ANNOTATION_REVIEWER_GROUP` with VIEW/CHANGE/ADD

---

### FR-5: Results Storage & Querying

Results are the core shared artifact. Both automated and human scoring
produce results in the same format — a dict of `{field_name: value}`
conforming to the assessment schema.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-5.1 | Store results with: item reference, scorer reference, schema reference, data dict, timestamp | Must |
| FR-5.2 | Tag each result with its source type (automated evaluator, human reviewer) | Must |
| FR-5.3 | Link results back to the originating session (via `ExperimentSession`) | Must |
| FR-5.4 | Support querying results by: session, scorer, schema, source type, date range | Must |
| FR-5.5 | **Include global session_id in all exports** (backlog #7) | Must |
| FR-5.6 | Support CSV and JSONL export of results | Must |
| FR-5.7 | Support CSV upload to correct/override results (with re-aggregation) | Should |
| FR-5.8 | Results are immutable after submission (corrections create new records or use explicit override flow) | Should |

**Existing code:**
- `EvaluationResult` model: [`apps/evaluations/models.py`](../../apps/evaluations/models.py) — stores `output` JSON with `message`, `generated_response`, `result` dict
- `Annotation` model: [`apps/human_annotations/models.py`](../../apps/human_annotations/models.py) — stores `data` JSON dict
- CSV export (evals): [`apps/evaluations/views/evaluation_config_views.py:download_evaluation_run_csv`](../../apps/evaluations/views/evaluation_config_views.py)
- CSV export (annotations): [`apps/human_annotations/views/queue_views.py:ExportAnnotations`](../../apps/human_annotations/views/queue_views.py)
- Result upload/correction: [`apps/evaluations/tasks.py:upload_evaluation_run_results_task`](../../apps/evaluations/tasks.py)

---

### FR-6: Aggregation & Trends

Aggregation computes statistical summaries across results. Both systems
already use identical aggregation logic.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-6.1 | Compute numeric aggregates: mean, median, min, max, stdev | Must |
| FR-6.2 | Compute categorical aggregates: mode, distribution (percentage per category) | Must |
| FR-6.3 | Exclude string fields from aggregation by default | Must |
| FR-6.4 | Respect per-field `use_in_aggregations` flag | Should |
| FR-6.5 | Recompute aggregates when new results are added | Must |
| FR-6.6 | Support trend analysis across multiple runs/time periods | Should |
| FR-6.7 | Aggregates should be filterable by source type (show automated-only, human-only, or combined) | Should |

**Existing code:**
- Aggregator framework: [`apps/evaluations/aggregators.py`](../../apps/evaluations/aggregators.py) — `MeanAggregator`, `MedianAggregator`, etc.
- Eval aggregation: [`apps/evaluations/aggregation.py:compute_aggregates_for_run`](../../apps/evaluations/aggregation.py)
- Annotation aggregation: [`apps/human_annotations/aggregation.py:compute_aggregates_for_queue`](../../apps/human_annotations/aggregation.py) — imports `aggregate_field` from evals
- Trend data builder: [`apps/evaluations/utils.py:build_trend_data`](../../apps/evaluations/utils.py)

---

### FR-7: Concordance Analysis (Backlog #1)

> *"Will be biggest manual lift for the team. Requires exporting all LLM
> evaluated sessions, and all annotation queue sessions, matching them, and
> analyzing how well they align (in external tool)"*

This is the top-priority cross-system feature. Compare automated vs human
scores on the same sessions to measure evaluator reliability.

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

**Existing code:** None — this is entirely new. Currently done manually via
external tooling.

**Prerequisite:** FR-5.3 (session-linked results) and FR-3.8 (session tagging).

---

### FR-8: Cross-System Data Flow (Backlog #5, #8, #9)

Data must flow bidirectionally between automated and human assessment
workflows.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-8.1 | **Import eval-scored sessions into an annotation queue** with filtering — e.g., "all sessions where automated score < threshold" (backlog #8) | Must |
| FR-8.2 | **Import annotated sessions into an eval dataset** with filtering — e.g., "all completed annotations" to use as ground truth (backlog #9) | Must |
| FR-8.3 | **Auto-populate annotation queue from eval tags** — sessions tagged by evals are automatically added to a configured queue (backlog #5) | Should |
| FR-8.4 | Preserve provenance when importing across systems (track that an annotation queue item originated from an eval run, or vice versa) | Should |
| FR-8.5 | Support filtering by eval result values when importing (e.g., "score < 3" or "category = 'poor'") | Should |

**Existing code:** None directly, but the building blocks exist:
- Session selection with filters: [`apps/human_annotations/views/queue_views.py:AnnotationQueueSessionsTableView`](../../apps/human_annotations/views/queue_views.py)
- Dataset creation from sessions: [`apps/evaluations/tasks.py:create_dataset_from_sessions_task`](../../apps/evaluations/tasks.py)
- `ExperimentSession.annotation_items` reverse relation: [`apps/human_annotations/models.py:134`](../../apps/human_annotations/models.py)

---

### FR-9: Session Tagging (Backlog #4)

> *"Enable evals to tag sessions or have outputs other than just the results
> dataset, so we can use those in filtering other places"*

Session tagging is the bridge that enables cross-system workflows. It may
belong as a generic capability rather than being evaluations-specific.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-9.1 | Tag sessions with key-value pairs from assessment results | Must |
| FR-9.2 | Tags are queryable for session filtering across the platform | Must |
| FR-9.3 | Automated evaluators can auto-tag sessions based on result values and configurable rules | Must |
| FR-9.4 | Tags include provenance (which assessment config, which run, when) | Should |
| FR-9.5 | Tags are visible on the session detail page | Should |
| FR-9.6 | Support tag-based triggers for downstream actions (e.g., add to annotation queue) | Should |

**Existing code:** Sessions have `state` (SanitizedJSONField) at
[`apps/experiments/models.py:1412`](../../apps/experiments/models.py) but no
dedicated tagging system. Chat messages have `metadata` (SanitizedJSONField) at
[`apps/chat/models.py:119`](../../apps/chat/models.py). A generic tagging
model would be new.

---

### FR-10: Access Control & Permissions

Assessment involves different roles with different access needs.

| ID | Requirement | Priority |
|----|------------|----------|
| FR-10.1 | Team admins can create/edit/delete assessment configs, schemas, evaluators | Must |
| FR-10.2 | Team admins can create/manage annotation queues and assign reviewers | Must |
| FR-10.3 | Reviewers can only see queues they're assigned to | Must |
| FR-10.4 | Reviewers can submit annotations and flag items, but not edit configs | Must |
| FR-10.5 | Anyone on the team can view results and aggregates | Should |
| FR-10.6 | Feature-gated behind a team-managed Waffle flag | Must |

**Existing code:**
- Feature flag: [`apps/teams/flags.py:57`](../../apps/teams/flags.py) — `HUMAN_ANNOTATIONS`
- Permission groups: [`apps/teams/backends.py:229-237`](../../apps/teams/backends.py) — `ANNOTATION_REVIEWER_GROUP`
- Queue visibility: [`apps/human_annotations/models.py:AnnotationQueueManager.visible_to`](../../apps/human_annotations/models.py)

---

## Non-Functional Requirements

| ID | Requirement | Notes |
|----|------------|-------|
| NFR-1 | Team-scoped — all models inherit `BaseTeamModel` ([`apps/teams/models.py:138`](../../apps/teams/models.py)) | Existing pattern |
| NFR-2 | Automated runs must not block the web process — use Celery for batch work | Existing pattern via chord |
| NFR-3 | Human annotation must be synchronous — no Celery for the review workflow | Existing pattern |
| NFR-4 | Export support for CSV and JSONL | Both systems already support this |
| NFR-5 | All JSON fields use `SanitizedJSONField` (null byte / control char safety) | Existing pattern |
| NFR-6 | Temporary evaluation sessions cleaned up after TTL | Existing: 30-day TTL in [`apps/evaluations/tasks.py`](../../apps/evaluations/tasks.py) |
| NFR-7 | Support team cloning (all assessment configs, schemas, datasets) | Evals already cloned in [`apps/teams/management/commands/clone_team.py`](../../apps/teams/management/commands/clone_team.py); annotations not yet |

## Backlog Item Mapping

Where each 2026 backlog item lands in the requirements:

| # | Backlog Item | Requirement(s) |
|---|-------------|----------------|
| 1 | LLM Eval vs human annotation concordance | **FR-7** (all) |
| 2 | Auto-add new sessions to Eval | FR-2.3 |
| 3 | Import entire sessions into evals | FR-2.4 |
| 4 | Evals tag sessions for filtering | **FR-9** (all) |
| 5 | Auto-add sessions to annotation queue from eval tags | FR-4.10, FR-8.3, FR-9.6 |
| 6 | Assign specific sessions to specific reviewers | FR-4.4 |
| 7 | Export CSV with global session_id | FR-5.5 |
| 8 | Import evals dataset into annotation queue | **FR-8.1** |
| 9 | Import annotation items into evals dataset | **FR-8.2** |

## Key Design Decisions to Make

These are the architectural choices that will shape the implementation. Each
is a discussion point for the design session.

### D-1: Shared schema model vs schema-per-system

**Option A — Shared `AssessmentSchema` model.** Both evaluators and annotation
queues reference the same schema object. Enables schema reuse and guarantees
concordance is comparing apples-to-apples.

**Option B — Keep schemas inline.** Evaluators define `output_schema`, queues
define `schema`, and concordance maps between them by field name. Simpler but
fragile.

### D-2: Unified result model vs adapter pattern

**Option A — Single `AssessmentResult` model.** Both automated and human
scoring write to the same table. Source type is a field. Concordance is a
simple query. Bidirectional import is trivial.

**Option B — Separate result models with a concordance adapter.** Each system
keeps its own result model. A concordance view queries both and joins on
session. Preserves existing schemas but adds query complexity.

### D-3: Session as first-class anchor vs message-level assessment

Currently evaluations work at the **message** level (`EvaluationMessage` per
human/AI pair) while annotations work at the **session** level
(`AnnotationItem` → `ExperimentSession`). The concordance requirement
(backlog #1) matches on session.

**Option A — Session-first.** The assessment unit is always a session. Message-
level results are nested within the session result. Simplifies concordance.

**Option B — Both levels.** Support both session-level and message-level
assessment. More flexible but more complex matching for concordance.

### D-4: Generic tagging vs assessment-specific tagging

Backlog #4 requires evals to tag sessions. This could be:

**Option A — Generic session tags.** A `SessionTag` model on `ExperimentSession`
usable by any system. Evals, annotations, pipelines, etc. can all tag sessions.
Tags become a platform-wide filtering mechanism.

**Option B — Assessment-specific tags.** Tags are a field on the result model.
Only assessment systems produce them. Simpler scope but less reusable.

### D-5: Extraction scope

**Option A — New `apps/assessment/` app.** Contains schemas, results,
aggregation, concordance, tagging. Evaluations and annotations become thin
workflow layers on top.

**Option B — New `apps/scoring/` app (minimal).** Only extract the already-shared
bits: `FieldDefinition`, aggregators, and add concordance. Evaluations and
annotations keep their own result models with a concordance adapter.

**Option C — Extend `apps/evaluations/`.** Add human scoring as another
evaluator type within the existing framework. Risks "god object" but avoids
new app overhead.
