# Score table and basic concordance — design

> First incremental step toward the [unified assessment system](../../design/unified-assessment.md). Introduces the design's `Score` value layer (lean shape), dual-write from existing `EvaluationResult` and `Annotation` records, a backfill for historical data, and a minimal concordance view that compares one categorical field across the two subsystems.

## Goal

Ship "basic concordance" between evaluations and human annotations — for the dogfood case of one shared binary `Choice` field — powered by the unified design's `Score` value layer rather than ad-hoc JSON joins. The dogfood pilot wants to compare a single binary choice across an LLM judge and a human annotation queue; this design serves that without locking in further decisions from the unified design that haven't been ratified yet.

Two outcomes:

1. A new `apps/assessments/` app containing a lean `Score` model that both `EvaluationResult` and `Annotation` write into.
2. A minimal concordance view at `/a/<team_slug>/evaluations/concordance/` that reads from `Score`, joins on `ExperimentSession`, and reports side-by-side values plus a simple agreement count for one shared categorical field.

## Out of scope (will land later, non-breakingly)

- `Assessment` umbrella, `AssessmentSchema` catalogue, `AssessmentRun`, `RoutingRule`, `AppliedRoutingRule`, `AppliedSourceFilter`.
- `score_config`, `assessment`, `assessment_run`, `participant` FKs on `Score`; `comment` field on `Score`.
- `USER_FEEDBACK` write path. (Source enum *defines* the value; no producer writes it yet.)
- Cohen's kappa, MAE, correlation, confusion matrix, bias, disagreement ranking, CSV export, trend charts.
- Schema unification (`AssessmentSchema`), field-name reconciliation, lifecycle hooks, cross-source aggregation.
- Persisted concordance configuration. Field matching is by name intersection at query time.

These are all carried in the [unified assessment design](../../design/unified-assessment.md); none is required for the dogfood pilot.

## Data model

A new app `apps/assessments/` housing one model.

### `Score`

```python
# apps/assessments/models.py
class Score(BaseTeamModel):
    class Source(models.TextChoices):
        LLM_JUDGE     = "llm_judge",     "LLM judge"
        PROGRAMMATIC  = "programmatic",  "Programmatic"
        HUMAN_REVIEW  = "human_review",  "Human review"
        USER_FEEDBACK = "user_feedback", "User feedback"  # reserved; no producer in v1
        SYSTEM        = "system",        "System"          # reserved; no producer in v1

    class DataType(models.TextChoices):
        NUMERIC     = "numeric",     "Numeric"
        CATEGORICAL = "categorical", "Categorical"
        BOOLEAN     = "boolean",     "Boolean"

    target_content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    target_object_id    = models.PositiveIntegerField()
    target              = GenericForeignKey("target_content_type", "target_object_id")

    name      = models.CharField(max_length=255)
    data_type = models.CharField(max_length=20, choices=DataType.choices)
    value_numeric = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    value_string  = models.TextField(null=True, blank=True)

    source = models.CharField(max_length=20, choices=Source.choices)

    automated_result = models.ForeignKey(
        "evaluations.EvaluationResult",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="scores",
    )
    review = models.ForeignKey(
        "human_annotations.Annotation",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="scores",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="scores",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["automated_result", "name"],
                condition=Q(automated_result__isnull=False),
                name="score_unique_per_automated_result_field",
            ),
            models.UniqueConstraint(
                fields=["review", "name"],
                condition=Q(review__isnull=False),
                name="score_unique_per_review_field",
            ),
            models.CheckConstraint(
                check=Q(value_numeric__isnull=False) | Q(value_string__isnull=False),
                name="score_value_present",
            ),
        ]
        indexes = [
            models.Index(fields=["target_content_type", "target_object_id", "name", "source"]),
            models.Index(fields=["created_at"]),
        ]
```

### Design notes

- **Field names align with the unified design** (`automated_result`, `review`, `Source`, `DataType`). When the eventual `EvaluationResult → AutomatedResult` and `Annotation → Review` renames happen, only the FK targets change; column names stay.
- **`target` is a `GenericForeignKey` from day one** even though only `ExperimentSession` is exercised in v1. Adding `Trace` and `EvaluationMessage` as future targets is non-breaking.
- **`target` is non-nullable** (composite PK fields are required). A Score must have a target — write-paths skip fields whose owning result/annotation lacks a session/message.
- **Partial unique constraints** on `(automated_result, name)` and `(review, name)` match the unified design's "artefact-level idempotency": re-running an `EvaluationResult` or resubmitting an `Annotation` deletes-and-recreates its Scores cleanly. The partial conditions are required because both FKs are nullable and we don't want them in the unique key for the other source's rows.
- **`team` is denormalised** (via `BaseTeamModel`) — set at write time from the parent `EvaluationResult.team` / `Annotation.team`. Lets queries scope by team without a join.
- **No `assessment` / `assessment_run` / `score_config` / `participant` / `comment`.** All deferred. Each is non-breaking to add later (nullable FK or nullable field).
- **`is_authoritative` is not denormalised onto `Score`.** Multi-reviewer queues let humans toggle authoritativeness post-submission (see `Annotation.authoritative_set_by` / `authoritative_set_at`), and `Annotation.is_authoritative` is auto-managed for single-reviewer queues. Denormalising would require a sync hook on every authoritative-toggle. The concordance view does a query-time join through `Score.review__is_authoritative` instead; cheap enough for the dogfood pilot. Revisit if it becomes a hot path.
- **`BOOLEAN` values land in `value_numeric` as 0/1**, not `value_string`. Lets future aggregation treat booleans as numeric without a special case. The `data_type=BOOLEAN` marker preserves the original intent so UI can render `True/False` rather than `1.0/0.0`.

## Write paths

### Automated side

A small helper invoked from wherever an `EvaluationResult` is created (today: the Celery evaluator task — `apps/evaluations/tasks.py`). Not in `EvaluationResult.save` itself, to avoid coupling persistence to side-effects fired by arbitrary callers.

```python
# apps/assessments/score_writers.py
def write_scores_from_evaluation_result(result: EvaluationResult) -> None:
    session = result.message.session  # may be None for CSV-imported messages
    if session is None:
        return  # v1 only targets ExperimentSession
    output = result.output or {}
    result_payload = output.get("result", {}) or {}  # evaluator output sits under "result"
    source = _source_for_evaluator(result.evaluator)  # LLM_JUDGE or PROGRAMMATIC
    schema = (result.evaluator.params or {}).get("output_schema", {}) or {}

    Score.objects.filter(automated_result=result).delete()  # idempotent re-run
    Score.objects.bulk_create([
        _score_from_field(
            team=result.team, target=session, name=name, raw_value=value,
            source=source, automated_result=result, schema_field=schema.get(name),
        )
        for name, value in result_payload.items()
    ])
```

`_source_for_evaluator` maps `Evaluator.type` to `Source.LLM_JUDGE` (default for unrecognised LLM evaluators) or `Source.PROGRAMMATIC` (Python evaluators). The mapping is a simple lookup keyed on the evaluator's registered name.

### Human side

Hook on `Annotation.save` when `status == SUBMITTED`. `Annotation.save` already does post-save bookkeeping (`_update_item_review_count`); adding one more "decompose into Scores" call there is consistent with the existing shape.

Scores are written for **every submitted annotation, regardless of `is_authoritative`**. Non-authoritative annotations are real reviewer judgments and we want to preserve them in `Score` for future inter-rater-reliability work (Story 9 in the unified design). The concordance view filters to authoritative at read time (see [Concordance view](#concordance-view)).

```python
# inside apps/human_annotations/models.py: Annotation.save
if is_new and self.status == AnnotationStatus.SUBMITTED:
    self._update_item_review_count()
    # local import: cross-app cycle (apps.assessments imports human_annotations.Annotation)
    from apps.assessments.score_writers import write_scores_from_annotation
    write_scores_from_annotation(self)
```

```python
def write_scores_from_annotation(annotation: Annotation) -> None:
    item = annotation.item
    # v1 only targets ExperimentSession. AnnotationItem.message points at ChatMessage,
    # which the unified design explicitly excludes as a Score target (D-13). Skip
    # message-only items rather than write data we'd have to migrate later.
    target = item.session
    if target is None:
        return
    schema = item.queue.schema or {}
    Score.objects.filter(review=annotation).delete()  # idempotent re-submission
    Score.objects.bulk_create([
        _score_from_field(
            team=annotation.team, target=target, name=name, raw_value=value,
            source=Score.Source.HUMAN_REVIEW, review=annotation, author=annotation.reviewer,
            schema_field=schema.get(name),
        )
        for name, value in (annotation.data or {}).items()
    ])
```

The local import inside `Annotation.save` is the project-sanctioned exception to the "no local imports" rule (AGENTS.md): `apps.assessments` already imports `human_annotations.Annotation` through its `Score.review` FK, so a top-level back-import would form a cycle.

### `_score_from_field` dispatch

Infers `data_type` and routes values:

- `bool` → `data_type=BOOLEAN`, stored as `value_numeric` 0 or 1.
- `int`, `float`, `Decimal` → `data_type=NUMERIC`, into `value_numeric`.
- `str` → `data_type=CATEGORICAL`, into `value_string`.
- If `schema_field` is passed and is a `Choice` field, `data_type=CATEGORICAL` regardless of Python type (forces categorical interpretation of numeric-looking choice values like `"0"` / `"1"`).
- `None`, `list`, `dict` → skipped with a warning log. Out of scope for v1.

## Backfill

Per [`docs/developer_guides/custom_migrations.md`](../../developer_guides/custom_migrations.md), using the two-phase pattern to avoid deploy timeouts on large datasets.

### Phase 1 (this PR)

1. Schema migration that creates the `Score` table.
2. Dual-write hooks active (Celery task + `Annotation.save`).
3. `IdempotentCommand` defined but not auto-run:

```python
# apps/assessments/management/commands/backfill_initial_scores.py
class Command(IdempotentCommand):
    help = "Backfill Score rows from existing EvaluationResults and submitted Annotations"
    migration_name = "backfill_initial_scores_2026_05_19"
    atomic = False  # per-team work is independent; let each row commit on its own

    def perform_migration(self, dry_run=False):
        from apps.evaluations.models import EvaluationResult
        from apps.human_annotations.models import Annotation, AnnotationStatus
        from apps.assessments.score_writers import (
            write_scores_from_evaluation_result, write_scores_from_annotation,
        )

        eval_qs = EvaluationResult.objects.select_related(
            "message__session", "evaluator", "team",
        )
        ann_qs = Annotation.objects.filter(status=AnnotationStatus.SUBMITTED).select_related(
            "item__queue", "item__session", "item__message", "team", "reviewer",
        )

        if dry_run:
            self.stdout.write(
                f"Would write Scores for {eval_qs.count()} eval results, {ann_qs.count()} annotations"
            )
            return

        written = 0
        for result in eval_qs.iterator(chunk_size=500):
            write_scores_from_evaluation_result(result)
            written += 1
        for annotation in ann_qs.iterator(chunk_size=500):
            write_scores_from_annotation(annotation)
            written += 1
        return written
```

After deploy: run `python manage.py backfill_initial_scores` manually.

### Phase 2 (follow-up PR)

Once Phase 1 is verified, ship a Django migration that auto-runs the command (with `force=True`) to top-up any rows created between manual run and follow-up deploy:

```python
# apps/assessments/migrations/0002_backfill_initial_scores_topup.py
operations = [
    RunDataMigration(
        "backfill_initial_scores_2026_05_19",
        command_options={"force": True},
    ),
]
```

Both `write_scores_from_*` helpers delete-then-create, so re-runs are safe overwrites.

## Concordance view

A new page under the existing Evaluations URL space — `/a/<team_slug>/evaluations/concordance/` — with a sub-item entry in the team sidebar under **Evaluations**.

### URL surface

```
GET /a/<team>/evaluations/concordance/                       → picker form
GET /a/<team>/evaluations/concordance/?eval=<id>&queue=<id>  → side-by-side view (auto-picks field if exactly one shared categorical)
GET /a/<team>/evaluations/concordance/?eval=<id>&queue=<id>&field=<name>
```

All state lives in query params. No persisted concordance config in v1.

### Data flow

1. Resolve `eval_config` and `annotation_queue` (404 if not in team).
2. Determine candidate fields: the **name intersection** of `eval_config.evaluators[*].params["output_schema"]` keys and `annotation_queue.schema` keys, narrowed to fields that are `Choice` type on both sides (or boolean). Numeric fields are silently filtered out for v1 — the data still flows into `Score`, just not into this view.
3. If `?field=` is set use it; else if exactly one candidate, use it (the dogfood path); else render a picker.
4. Run two `Score` queries:

   ```python
   session_ct = ContentType.objects.get_for_model(ExperimentSession)

   judge_scores = (
       Score.objects.filter(
           team=team,
           target_content_type=session_ct,
           name=field_name,
           source__in=[Score.Source.LLM_JUDGE, Score.Source.PROGRAMMATIC],
           automated_result__evaluator__in=eval_config.evaluators.all(),
       )
       .order_by("target_object_id", "-created_at")
   )
   human_scores = (
       Score.objects.filter(
           team=team,
           target_content_type=session_ct,
           name=field_name,
           source=Score.Source.HUMAN_REVIEW,
           review__item__queue=annotation_queue,
           review__is_authoritative=True,  # ground-truth filter
       )
       .order_by("target_object_id", "-created_at")
   )
   ```

   The `review__is_authoritative=True` filter ensures concordance compares the judge against the resolved human answer. For single-reviewer queues every submitted annotation is auto-marked authoritative (see `Annotation._maybe_auto_mark_authoritative`), so this filter is a no-op there. For multi-reviewer queues, items in `AWAITING_RESOLUTION` (no authoritative pick yet) naturally drop out of the comparison until a resolver picks one.

5. Pre-aggregate per `(target, source)` in Python — for v1, keep the **latest** Score per side per session. On the judge side this is the most recent run's output; on the human side the authoritative filter usually leaves exactly one row per session, but the same "pick latest" rule applies for the rare case where an item's authoritative pick has changed and old Score rows weren't cleaned up. This is the simple stand-in for the unified design's per-source consensus (mean / mode). A code comment flags it as v1-only behaviour.
6. Join on `target_object_id`. Three buckets: matched, eval-only, human-only.
7. Compute `agree = judge_value == human_value` per matched row.

### Rendering

Standard Django template with HTMX form swap (consistent with the rest of OCS). Summary header plus a paginated table:

```
Concordance: <eval_config.name> vs <annotation_queue.name>
Field: <field_name>

Matched: 42        Agreement: 35 / 42 (83%)
Eval only: 8       Human only: 3

| Session     | Judge value | Human value | Agree? |
| <ext_id 1>  | yes         | yes         | ✓      |
| <ext_id 2>  | no          | yes         | ✗      |
| ...         |             |             |        |
```

Each session row links to the existing session detail page, so reviewers can dig into disagreements without leaving the team.

### Discoverability

- Sidebar sub-item under **Evaluations**, labeled "Concordance".
- Gated behind a new team-managed waffle flag `ASSESSMENTS_CONCORDANCE`. Visible only when the flag is on **and** both `EVALUATIONS` and `HUMAN_ANNOTATIONS` flags are on (you can't compare what you can't produce).

### What this view does not do

Explicitly out of scope for the code, not just the UI:

- No kappa, MAE, correlation, confusion matrix, bias metrics.
- No CSV / JSONL export.
- No filtering by date range, tag, experiment version, or participant.
- No multi-field comparison on one screen — one field at a time.
- No persistence of the chosen `(eval, queue, field)` tuple.

## Testing

Per AGENTS.md ("when adding new features: write or update unit tests first, then code to green"):

- **Score-writer unit tests** (`apps/assessments/tests/test_score_writers.py`):
  - `write_scores_from_evaluation_result`: typical LLM output decomposes into one Score per field; numeric/bool/string values land in the right column with the right `data_type`; `None`/list/dict values are skipped with a log; missing `result.message.session` is a no-op; re-run deletes-and-creates idempotently.
  - `write_scores_from_annotation`: typical annotation `data` dict decomposes correctly; targets `item.session` (annotations on message-only items are skipped per D-13); resubmission overwrites; missing target is a no-op; non-submitted annotations don't write (handled by the `Annotation.save` guard, but assert at this layer too for safety).
- **Backfill command test** (`apps/assessments/tests/test_backfill_command.py`):
  - Dry-run reports correct candidate counts.
  - Real run produces the same Score rows as live dual-write for representative fixtures.
  - Re-run is a no-op against an already-backfilled DB (delete-and-create idempotency proven through total row count).
- **Concordance view test** (`apps/assessments/tests/test_concordance_view.py`):
  - With two `Evaluator`s in one `EvaluationConfig` plus one `AnnotationQueue` sharing one categorical field over a small set of sessions, the view produces the expected matched / eval-only / human-only buckets and agreement count.
  - Multi-reviewer queue: an item with one authoritative annotation and one non-authoritative annotation shows the **authoritative** value as the human side. Items in `AWAITING_RESOLUTION` (no authoritative pick) are excluded.
  - 404 when configs aren't in the request team.
  - Empty state (no Score rows) renders sensibly.
  - Permission test: the view is hidden when the waffle flag is off.

Integration with existing eval / annotation tests: none required. The new write hooks are guarded such that even an empty/unexpected `output` payload produces no Score rows (and zero exceptions), so existing tests remain green without modification.

## Sequencing summary

1. Create `apps/assessments/` (model + `score_writers.py` + management command + tests). Schema migration creates `Score`.
2. Wire the Celery task to call `write_scores_from_evaluation_result` after `EvaluationResult.objects.create(...)`.
3. Wire `Annotation.save` to call `write_scores_from_annotation` on first SUBMITTED save.
4. Add the concordance view + URL + template + sidebar sub-item + waffle flag.
5. Deploy. Run `python manage.py backfill_initial_scores` manually.
6. Follow-up PR: Django migration with `RunDataMigration(..., force=True)` for the top-up. ([Phase 2 in `custom_migrations.md`](../../developer_guides/custom_migrations.md#phase-2-add-django-migration-top-up).)
