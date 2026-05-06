## 1. Schema and migrations

- [x] 1.1 Add `DatasetAutoPopulationRule` model in `apps/evaluations/models.py` (FK to `EvaluationDataset`, FK to source `Experiment`, `evaluation_mode`, `filter_query` text, `is_enabled`, `last_ingested_at`, `last_run_at`, `last_run_status`, `last_error`, `consecutive_failure_count`); inherit from `BaseTeamModel`.
- [x] 1.2 Add `DatasetIngestionEntry` model (FK to `DatasetAutoPopulationRule`, `source_session_id`, `source_message_id`, FK to `EvaluationMessage`, `created_at`); add unique constraints on `(rule, source_message_id)` for message-mode and `(rule, source_session_id)` for session-mode (use partial unique indexes or two indexes).
- [x] 1.3 Add `EvaluationConfig.auto_run_on_append` boolean field (default `False`).
- [x] 1.4 Add `EvaluationRunType.DELTA = "delta"` choice and an `EvaluationRun.scoped_messages` M2M to `EvaluationMessage`.
- [ ] 1.5 Add Waffle flag `evaluations.auto_populate_datasets` and gate rule create/update views and beat-task processing behind it. (Flag registered in chunk A; view-level gating added in chunk B; beat-task gating lands with chunk C.)
- [x] 1.6 Generate Django migration via `uv run python manage.py makemigrations evaluations`; verify backwards-compatibility (no defaults that require backfill, all new fields nullable or with sane defaults).
- [x] 1.7 Add admin registrations for `DatasetAutoPopulationRule` and `DatasetIngestionEntry`.

## 2. Forms, views, and URLs

- [x] 2.1 Add `DatasetAutoPopulationRuleForm` in `apps/evaluations/forms.py` with validation: source experiment in same team, `evaluation_mode` matches dataset's mode, filter query parses cleanly via `FilterParams(QueryDict(...))`.
- [x] 2.2 Add list/create/edit/delete views in `apps/evaluations/views/dataset_views.py` (or a new `auto_population_views.py`) following existing CBV patterns and team scoping.
- [x] 2.3 Wire URL patterns in `apps/evaluations/urls.py`; ensure `get_absolute_url` on the rule returns to its dataset.
- [x] 2.4 Update dataset detail template to list rules with status fields (last run, status, error, contributed-message count via `DatasetIngestionEntry` count).
- [x] 2.5 Add `auto_run_on_append` checkbox to the existing `EvaluationConfig` form with help text describing cost implications.

## 3. Ingestion task

- [ ] 3.1 Add `auto_populate_eval_datasets` periodic task in `apps/evaluations/tasks.py`: iterate enabled rules ordered by `last_run_at` ascending, lock each via `select_for_update(skip_locked=True)`, and dispatch to a per-rule helper.
- [ ] 3.2 Implement `_ingest_rule(rule)` helper: build the appropriate filter (`ChatMessageFilter` for message mode, `ExperimentSessionFilter` for session mode), apply the rule's `filter_query`, restrict to source experiment + team, restrict to `created_at > last_ingested_at - safety_margin`, exclude sources already in `DatasetIngestionEntry` for this rule, batch the matches, and reuse `make_evaluation_messages_from_sessions` to build `EvaluationMessage` rows.
- [ ] 3.3 In a single transaction per rule: append new `EvaluationMessage` rows to the dataset's M2M, write `DatasetIngestionEntry` rows, bump `last_ingested_at` to `max(source.created_at)` of the batch, set `last_run_status="success"`, reset `consecutive_failure_count`.
- [ ] 3.4 On exception: set `last_run_status="error"`, store `last_error`, increment `consecutive_failure_count`; if it reaches 3, set `is_enabled=False` and emit a notification via `apps.ocs_notifications`. Catch per-rule so other rules in the tick are unaffected.
- [ ] 3.5 Register the task in `CELERY_BEAT_SCHEDULE` (or via `django_celery_beat` periodic task fixture/data migration) at a 5-minute interval; gate registration behind the Waffle flag at task entry.

## 4. Auto-trigger of delta evaluation runs

- [ ] 4.1 Extract a small `append_messages_to_dataset(dataset, messages)` helper that performs the M2M append and returns the appended `EvaluationMessage` set; refactor existing dataset-creation paths in `apps/evaluations/tasks.py` to call it.
- [ ] 4.2 In that helper, after a successful append, query `EvaluationConfig.objects.filter(dataset=dataset, auto_run_on_append=True)` and enqueue one delta run per match by calling `EvaluationConfig.run(run_type=EvaluationRunType.DELTA, scoped_messages=appended)`.
- [ ] 4.3 Update `EvaluationConfig.run` to accept `scoped_messages`; persist them on the new `EvaluationRun.scoped_messages` M2M before delegating to `run_evaluation_task`.
- [ ] 4.4 Update `run_evaluation_task` to dispatch per-message work over `scoped_messages` when present, falling back to the dataset's full membership otherwise; preserve preview-sample behaviour for `PREVIEW` and full-dataset behaviour for `FULL`.
- [ ] 4.5 Confirm tag-rule application path (`_maybe_apply_tag_rules`) treats `DELTA` runs the same as `FULL` (i.e., not skipped like preview).

## 5. UI surfacing of delta runs

- [ ] 5.1 Update evaluation run history table/template to render the run `type` (full / preview / delta) and the scoped message count for delta runs.
- [ ] 5.2 Update results page to scope its display to `scoped_messages` when present so a delta run shows only the rows it evaluated.

## 6. Tests

- [ ] 6.1 Unit-test `DatasetAutoPopulationRuleForm` validation: mode mismatch, cross-team experiment, malformed filter query.
- [ ] 6.2 Integration-test `_ingest_rule` happy path (message mode and session mode), idempotent re-run (no duplicates), no-op on empty window, failure path increments `consecutive_failure_count`, auto-disable at 3 failures emits a notification.
- [ ] 6.3 Concurrency test: simulate two workers picking up the same rule and assert exactly one performs the work (use `select_for_update` semantics or a behavioural test with `transaction.atomic`).
- [ ] 6.4 Delta-run tests: `EvaluationConfig.run(run_type=DELTA, scoped_messages=...)` produces one `EvaluationRun` with the expected scope; `run_evaluation_task` evaluates only scoped messages even after the dataset gains more rows mid-flight.
- [ ] 6.5 Auto-trigger tests: appending messages to a dataset enqueues a delta run for opted-in configs and skips opted-out configs; manual filter-import path also fires the trigger.
- [ ] 6.6 Migration test: apply / rollback verifies no data loss and that existing evaluation flows still work.
- [ ] 6.7 Run targeted suite: `uv run pytest apps/evaluations/tests -v` and lint/typecheck the touched files (`uv run ruff check apps/evaluations --fix`, `uv run ruff format apps/evaluations`, `uv run ty check apps/evaluations`).

## 7. Docs and rollout

- [ ] 7.1 Add a section to the relevant `docs/` page (or new `docs/agents/...` if appropriate) describing rule creation, the cost implications of `auto_run_on_append`, and the forward-only semantics of the high-water mark.
- [ ] 7.2 Update `docs/developer_guides/feature_flags.md` to document `evaluations.auto_populate_datasets`.
- [ ] 7.3 PR uses `.github/pull_request_template.md`; check the migrations-backwards-compatible box; include a Demo recording of creating a rule and observing a delta run.
- [ ] 7.4 Staging soak: enable the flag for one team, create a single rule against a low-traffic experiment, verify ingestion and a delta run; then enable broadly and remove the flag in a follow-up change.
