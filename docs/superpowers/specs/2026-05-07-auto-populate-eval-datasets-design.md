# Auto-populate Eval Datasets — Design

GitHub issue: [dimagi/open-chat-studio#3044](https://github.com/dimagi/open-chat-studio/issues/3044).

## Goal

Let teams configure an `EvaluationDataset` to continuously ingest new sessions
from a source bot via filter criteria, and optionally auto-run linked
evaluation configs over only the new rows.

Today, datasets are populated one-shot (manual session pick, filter-driven
import, or CSV upload). They go stale unless someone refreshes them. v1 lifts
that constraint via polling-based ingestion plus an opt-in auto-run hook.

## Scope

In scope for v1:

- Per-dataset auto-population rules (1 dataset → N rules) that ingest new
  sessions/messages from one source experiment matching saved filter
  criteria.
- Periodic Celery Beat task that walks enabled rules, brute-force scans
  each rule's source experiment within a bounded lookback window, and
  dedupes against existing dataset rows.
- Per-config opt-in (`EvaluationConfig.auto_run_on_append`) that fires a
  delta evaluation run scoped to just the newly appended rows. Only the
  auto-population path triggers this in v1 — manual filter-import and CSV
  import do not.
- UI surfacing: rule list/edit on dataset detail; auto-run toggle on
  evaluation config; run-type badge + scoped count on run history.

Out of scope for v1:

- Lifecycle-hook trigger (signal on `ExperimentSession.end()`) — polling
  only.
- "Auto-eval finishes" entry point as a distinct trigger. Late-tagged
  sessions are picked up by the next polling tick *if* they fall back into
  the rule's filter and were not previously ingested into this dataset.
- Sampling sessions (the issue mentions it but it is deferred).
- Manual filter-import or CSV-import paths auto-triggering linked evals.
  (Could be made optional later — the issue's design says any append should
  trigger, but for v1 we keep the auto-trigger scoped to auto-population
  only so manual workflows are unaffected.)
- Backfilling historical traffic when a rule is created (forward-only).
- Rolling-window dataset size caps, retention, eviction.
- Real-time (push) ingestion.

## Schema

### New: `DatasetAutoPopulationRule` (in `apps/evaluations/models.py`)

`BaseTeamModel`. Fields:

| field | type | notes |
|---|---|---|
| `dataset` | FK → `EvaluationDataset` (CASCADE) | parent |
| `source_experiment` | FK → `experiments.Experiment` (CASCADE) | which bot to ingest from |
| `filter_query_string` | TextField, blank=True | rehydrated via `FilterParams(QueryDict(...))`. Empty string = "all sessions from this bot" |
| `is_enabled` | BooleanField, default `True` | manual on/off |
| `last_run_at` | DateTimeField, null=True | when the polling task last touched this rule |
| `last_run_status` | choices `success` / `error` / `no_op`, null=True | most recent tick outcome |
| `last_error` | TextField, blank | error message from last failure |
| `consecutive_failure_count` | PositiveSmallIntegerField, default 0 | drives auto-disable |

No high-water mark field is needed: the rule's own `created_at` (inherited
from `BaseTeamModel`) acts as the forward-only floor, and dedup is handled
at scan time via `NOT IN dataset.messages` (see "Ingestion task").

`clean()` enforces `team` matches both `dataset.team` and
`source_experiment.team`. Mode-matching (`rule.dataset.evaluation_mode` is
respected when applying the filter) is enforced by the form, not stored on
the rule.

### Modifications

- `EvaluationConfig.auto_run_on_append: bool` (default `False`).
- `EvaluationRunType.DELTA = "delta"` choice.
- `EvaluationRun.scoped_messages: M2M(EvaluationMessage)` — empty for
  `FULL` / `PREVIEW`; populated at enqueue for `DELTA`.

### Waffle flag

No new flag. The whole feature is gated by the existing `flag_evaluations`
— a team that has access to the evaluations app gets auto-population
without a separate opt-in.

### Migration

All schema changes are additive; no backfill needed. Existing manual
workflows are unaffected.

## Ingestion task

Periodic Celery Beat task `auto_populate_eval_datasets` (in
`apps/evaluations/tasks.py`), scheduled every 5 minutes via a
`django_celery_beat.PeriodicTask` row created in a data migration (the
project's beat scheduler is `django_celery_beat.schedulers:DatabaseScheduler`,
so beat schedules live in the DB, not `settings.CELERY_BEAT_SCHEDULE`):

```
for rule in DatasetAutoPopulationRule.objects.filter(is_enabled=True).order_by("last_run_at"):
    try:
        with transaction.atomic():
            lock rule via select_for_update(skip_locked=True); skip if locked
            _ingest_rule(rule)
    except Exception as e:
        handle_failure(rule, e)
```

The polling task itself does not check the Waffle flag — only teams with
`flag_evaluations` enabled can create rules in the first place, so a tick
naturally has nothing to do for non-evaluations teams.

### `_ingest_rule(rule)`

The rule does **not** carry a high-water mark. Each tick brute-force scans
the source experiment's recent sessions and relies on `NOT IN dataset` for
dedup. This catches sessions that gain a matching tag (or other late
state-change) long after creation, since `CustomTaggedItem` writes don't
bump `session.updated_at`.

1. Pick filter class by `rule.dataset.evaluation_mode`:
   - `message` → `ChatMessageFilter`
   - `session` → `ExperimentSessionFilter`
2. Build base queryset scoped to `rule.team` and `rule.source_experiment`
   (and child versions, matching how the existing import handles versioned
   experiments).
3. Apply `FilterParams(QueryDict(rule.filter_query_string))` if non-empty.
4. Apply the lookback window:
   `created_at > MAX(rule.created_at, now() - LOOKBACK_DAYS)`. `LOOKBACK_DAYS`
   is a Django setting (default 30); `rule.created_at` is the forward-only
   floor (a brand-new rule never picks up sessions older than itself).
5. Apply NOT IN dedup against `rule.dataset.messages`:
   - session mode → exclude sessions whose `id` is already in
     `dataset.messages.values_list("session_id")`.
   - message mode → exclude
     `(input_chat_message_id, expected_output_chat_message_id)` pairs
     already on the dataset (mirrors the existing dedup in
     `create_dataset_from_session_messages_task`).
6. Build `EvaluationMessage` rows via the existing helpers
   (`make_session_evaluation_messages` for session mode,
   `EvaluationMessage.create_from_sessions` for message mode).
7. If batch is empty → record `last_run_status = no_op`, return.
8. Otherwise, in the same transaction:
   - `bulk_create` messages, append to `dataset.messages`.
   - set `last_run_status = success`, `last_run_at = now()`, reset
     `consecutive_failure_count`.
9. After commit, invoke the auto-trigger (see "Auto-trigger" below) with
   the appended messages.

### Failure handling

On exception inside `_ingest_rule`:

- `consecutive_failure_count += 1`.
- store `last_error`, set `last_run_status = error`, `last_run_at = now()`.
- if `consecutive_failure_count == 3`: set `is_enabled = False` and emit a
  notification via `apps.ocs_notifications`.

Each rule has its own try/except — one bad rule never poisons the rest of a
tick.

## Auto-trigger of delta evaluation runs

The auto-trigger is wired only into `_ingest_rule`; manual filter-import
and CSV-import paths are intentionally left untouched in v1. Existing
append code in `create_dataset_from_sessions_task` and
`create_dataset_from_session_messages_task` is **not** refactored — making
those paths trigger evaluations is a future, opt-in change.

After a successful auto-population append `_ingest_rule` queries:

```python
configs = EvaluationConfig.objects.filter(dataset=dataset, auto_run_on_append=True)
for config in configs:
    config.run(run_type=EvaluationRunType.DELTA, scoped_messages=appended)
```

`EvaluationConfig.run` is updated to accept an optional
`scoped_messages: list[EvaluationMessage]`. When present, the helper
populates `EvaluationRun.scoped_messages` M2M before delegating to
`run_evaluation_task`.

`run_evaluation_task` is updated: when the run has `scoped_messages`
populated, dispatch per-message work over those messages; otherwise current
behaviour applies (full dataset for `FULL`, sample for `PREVIEW`).

Tagging behaviour: `_maybe_apply_tag_rules` already gates only on
`EvaluationRunType.PREVIEW`. `DELTA` is not in that branch so evaluator-tag
rules apply for delta runs the same as for full runs.

## UI

### Dataset detail (`evaluations/dataset_edit.html`)

New "Auto-population rules" panel:

- list of rules with columns: source experiment, enabled, last run
  (timestamp + status badge), last error, contributed-row count
  (`dataset.messages.filter(session__experiment=rule.source_experiment).count()`
  for v1 — cheap, slightly imprecise under filter changes).
- buttons: add rule, edit, toggle enabled, delete.

### Rule create/edit form

- source experiment dropdown (team-scoped).
- enabled checkbox.
- embedded filter UI reusing `get_filter_context_data` with the filter class
  chosen by the dataset's mode.
- validation: mode-aware filter, cross-team source experiment rejected,
  malformed filter query rejected.

### Evaluation config form

- new `auto_run_on_append` checkbox with help text noting cost
  implications.

### Run history + results

- run-type badge (`full` / `preview` / `delta`); for `DELTA` also show the
  scoped row count.
- on the results page, when the run has `scoped_messages`, the results
  table only shows those messages.

## Tests

- Form validation: mode mismatch, cross-team source experiment, malformed
  filter query.
- Ingestion happy paths (session and message mode), no-op on empty window,
  NOT IN dedup against existing rows, idempotent re-run.
- Failure path: counter increments, auto-disables at 3 consecutive
  failures, emits a notification.
- Concurrency: `select_for_update(skip_locked=True)` ensures two concurrent
  ticks process a rule at most once.
- Delta run scope is preserved when more rows are appended to the dataset
  mid-flight (the run still evaluates only the original scope).
- Auto-trigger fires for opted-in configs only when the auto-population
  task appends rows; manual filter-import and CSV-import paths do **not**
  fire the auto-trigger.

## Open questions

1. **Filter-on-dataset vs filter-on-eval-config.** Issue notes this should
   be confirmed with the Connect team. v1 picks dataset-level (matches the
   issue's lean and our recent answers) but the question stays open until
   the Connect team confirms.

2. **`LOOKBACK_DAYS` default.** Starting at 30 days. If a tag is added more
   than 30 days after a session was created, the rule will not ingest it.
   Revisit after we see real usage; the brute-force scan cost grows
   linearly with this value.

## Future changes (not v1)

- **Event-based triggers (Option B in design discussion).** Hook
  `CustomTaggedItem` post_save / post_delete and
  `apps.evaluations.tagging.apply_rules_to_result` to enqueue immediate
  rule-checks for affected datasets. Lets us drop or shrink `LOOKBACK_DAYS`
  while still catching post-hoc tag changes. Keep in mind the issue
  explicitly calls out "auto-eval finishes" as one of the two practical
  entry points; this is where it would land.
- **Sampling.** Per-rule "ingest only N% of matching sessions" knob.
- **Lifecycle-hook trigger on `ExperimentSession.end()`** — for
  near-real-time ingestion when the polling cadence is too slow.
- **Manual / CSV import path auto-triggering** — make the trigger optional
  on those paths so users can opt in per dataset or per import.
