## Context

Open Chat Studio's evaluation subsystem (`apps.evaluations`) lets teams build datasets of `EvaluationMessage` rows and run `EvaluationConfig`s (a dataset + evaluators + an experiment version) against them via Celery (`run_evaluation_task`). Datasets are populated by:
1. Manual selection of session IDs in the UI.
2. Filter-driven import using `ChatMessageFilter` / `ExperimentSessionFilter` and `FilterParams` (existing `apps/web/dynamic_filters` infrastructure).
3. CSV import.

Each path is one-shot: the user clicks, messages are imported, the dataset is frozen until the next manual refresh. There is no background process that watches for new traffic, no provenance link from a dataset row back to the rule that introduced it, and no way to say "evaluate this config when the dataset grows."

The platform already has the building blocks needed:
- `django_celery_beat` is enabled (`config/settings.py:475`) so periodic tasks are first-class.
- `ChatMessageFilter` / `ExperimentSessionFilter` already produce the exact querysets we need; they are reused for manual import in `EvaluationDataset.create_from_sessions`.
- `EvaluationMessage` already carries FK references to `ChatMessage` and `ExperimentSession`, so provenance to the source row is already modelled.
- `run_evaluation_task` already iterates over a dataset's messages and dispatches per-message Celery tasks; a delta variant can reuse most of it.

## Goals / Non-Goals

**Goals:**
- Let a team configure one or more **auto-population rules** per dataset that ingest new sessions/messages from a source experiment matching saved filter criteria.
- Guarantee idempotent ingestion: a given source ChatMessage / ExperimentSession is appended to a given dataset by a given rule **at most once**.
- Let a team mark an `EvaluationConfig` as **auto-run on dataset append**, so that whenever new rows are added (by any path, including auto-population), a Celery-backed evaluation run executes against just the new rows.
- Make ingestion and triggered runs observable: surface last-run timestamp, count appended, and failure detail on the dataset detail UI.
- Stay within current operational and authorization patterns (team scoping via `BaseTeamModel`, Waffle flags where needed, Celery Beat for periodic work).

**Non-Goals:**
- Real-time, push-based ingestion (e.g., reacting to every chat message via signals or static triggers). A short polling interval is sufficient and avoids tight coupling to the chat hot path.
- Rolling-window dataset size caps, retention policies, or eviction. v1 simply appends.
- Backfilling prior history when a rule is created. The rule's high-water mark starts at `created_at`; users can still run a manual import for historical data.
- Cross-team or cross-experiment dedup. A rule is scoped to one experiment in one team.
- Re-evaluating dataset rows after the rule's filter changes. Filter changes only affect future ingestion.

## Decisions

### D1. Polling-based ingestion via Celery Beat (not signal-driven)

**Choice:** Add a Celery Beat periodic task `auto_populate_eval_datasets` that runs every N minutes (default 5), iterates enabled rules, and ingests anything newer than the rule's high-water mark.

**Alternatives considered:**
- Hook into the existing `apps.events` `StaticTriggerType` machinery (e.g., a new `NEW_MESSAGE_INGESTED` trigger). Rejected: couples ingestion to the chat-handling hot path, increases per-message overhead, and complicates retries.
- Listen on a Django `post_save` signal for `ChatMessage`. Rejected for the same reason — signal handlers run inline with the request that wrote the message.

**Why polling wins:** the chat hot path stays untouched; failures and retries fall back to Celery's existing semantics; ingestion can be batched per rule for efficiency; Celery Beat is already wired up.

### D2. High-water mark per rule, with a `(rule, source_id)` dedup table

**Choice:** Each `DatasetAutoPopulationRule` stores a `last_ingested_at` timestamp, used to scope queries (`ChatMessage.created_at > last_ingested_at` in message mode; `ExperimentSession.last_message_created_at > last_ingested_at` in session mode). After successful ingestion, the timestamp is bumped to `max(created_at)` of the batch.

In addition, a `DatasetIngestionEntry` model records `(rule, source_session_id, source_message_id, evaluation_message_id, created_at)` with a unique constraint on `(rule, source_message_id)` (message mode) or `(rule, source_session_id)` (session mode). The ingestion task consults this table before inserting.

**Why both:** the timestamp is the cheap forward filter that keeps each tick's queryset small. The entry table is the safety net that makes the operation strictly idempotent even if the timestamp is bumped incorrectly (clock skew, late-arriving rows, partial failure mid-batch). Without the entry table, a crash mid-batch could double-import; without the timestamp, every tick scans the full chat history.

**Alternative considered:** rely solely on `(dataset, source_message_id)` uniqueness on `EvaluationMessage`. Rejected because manual imports and CSV imports legitimately want to allow the same `ChatMessage` to be represented in multiple datasets, and even multiple times within one dataset for different evaluation purposes — the uniqueness lives at the *rule* layer, not the dataset layer.

### D3. Filter persistence reuses `FilterParams` query-string serialization

**Choice:** Store the rule's filter as a `TextField` containing the same query-string form already used by `FilterParams.from_request()` and persisted on dataset records (see `apps/evaluations/tasks.py:855` — `FilterParams(QueryDict(filter_query))`). The form layer renders the existing dynamic-filter UI; the task layer rehydrates with `FilterParams(QueryDict(...))` and applies the appropriate filter class.

**Why:** zero new filter abstraction to introduce, exact parity with manual filter-import behaviour, no risk of two filter dialects diverging.

### D4. Delta evaluation runs are a new `EvaluationRun.type`

**Choice:** Add `EvaluationRunType.DELTA = "delta"` alongside the existing `FULL` and `PREVIEW`. A delta run carries an explicit set of `EvaluationMessage` IDs in scope (stored via a new `EvaluationRun.scoped_messages` M2M, populated at enqueue time). The Celery task dispatches per-message work over the scoped set if present, otherwise falls back to the dataset's full membership (current behaviour).

**Alternatives considered:**
- Reuse `FULL` and pass the message subset through `kwargs`. Rejected: harder to reason about historical runs in the UI, no audit trail of *what* was in scope.
- Per-message FK from `EvaluationResult` already records what was evaluated, so the M2M is technically redundant. Kept anyway because it lets us answer "what was the *intent* of this run?" without joining through results, and lets the UI render scope before any result rows exist.

### D5. Auto-run subscription lives on `EvaluationConfig`, not on the dataset

**Choice:** Add `EvaluationConfig.auto_run_on_append: bool` (default `False`). When auto-population (or any future append path) commits new messages, the ingestion logic queries `EvaluationConfig.objects.filter(dataset=dataset, auto_run_on_append=True)` and enqueues one delta run per matching config.

**Why:** One dataset can back multiple configs (different evaluators, different experiment versions). A given team may want some configs to auto-run and others to remain manual. Putting the toggle on the dataset would force all-or-nothing.

### D6. Rule lifecycle and failure surfacing

**Choice:** `DatasetAutoPopulationRule` carries `is_enabled`, `last_run_at`, `last_run_status` (`success` / `error` / `no_op`), and `last_error` text. The periodic task wraps each rule in its own try/except so a single bad rule doesn't poison the batch. After three consecutive failures the rule auto-disables itself and surfaces an admin notification (reuse `apps.ocs_notifications`).

**Why:** keeps a misconfigured rule (e.g., a filter referencing a deleted tag) from spinning forever and silently failing.

### D7. Reuse `make_evaluation_messages_from_sessions` for ingestion

**Choice:** The ingestion task collects matching ChatMessages by external session id, dedupes against `DatasetIngestionEntry`, then calls the existing `make_evaluation_messages_from_sessions` helper. The returned messages are appended to the dataset's M2M and an `DatasetIngestionEntry` row is written per source.

**Why:** preserves all existing semantics around context capture (`participant_data`, `session_state`, `history`), so auto-populated rows are indistinguishable in shape from manually imported ones — meaning evaluators don't need to special-case them.

## Risks / Trade-offs

- **[Cost amplification]** Auto-running evaluators on every batch can trigger many LLM calls. → Mitigation: `auto_run_on_append` is opt-in per config, and the delta run only evaluates new rows. Document expected cost in the form help text.
- **[Filter performance]** Polling every 5 minutes per rule could drift into N+1 territory if a team creates dozens of rules. → Mitigation: each tick batches by rule but uses `select_related` / `prefetch_related` consistent with `EvaluationMessage.create_from_sessions`. Add a soft cap (e.g., 50 rules / team) enforced in the form, revisited if real usage demands more.
- **[Late-arriving data]** A ChatMessage written with a timestamp slightly older than `last_ingested_at` (clock skew, retry replay) could be missed by the timestamp filter. → Mitigation: query window is `created_at > last_ingested_at - safety_margin` (e.g., 60 seconds), with the dedup table preventing double-insert.
- **[Unbounded dataset growth]** A popular experiment will grow its dataset indefinitely. → Mitigation: out of scope for v1; expose `messages.count()` prominently in the UI and note the limitation in docs. A retention policy is a follow-up change.
- **[Auto-disable surprise]** A rule that auto-disables after three failures is operator-friendly but may be invisible. → Mitigation: emit an `apps.ocs_notifications` notification on auto-disable; show a banner in dataset detail.
- **[Migration order]** The `DatasetIngestionEntry` model and the new `EvaluationRunType.DELTA` choice must ship before the periodic task is registered, or the first beat tick can fail. → Mitigation: gate task registration behind a migration-applied check at app startup, and ship the task in a follow-up deploy after confirming migrations.
- **[Concurrency]** Two beat workers picking up the same rule simultaneously could double-process. → Mitigation: wrap each rule's processing in `select_for_update(skip_locked=True)` on the rule row at the start of its transaction.

## Migration Plan

1. Ship migration adding `DatasetAutoPopulationRule`, `DatasetIngestionEntry`, `EvaluationRun.scoped_messages` M2M, `EvaluationConfig.auto_run_on_append`, and the new `EvaluationRunType.DELTA` choice. No data backfill needed.
2. Ship form/view code (no celery task yet). Users can create rules; nothing runs them.
3. Ship and enable the Celery Beat periodic task in a follow-up deploy. Verify by creating a single rule against a low-traffic experiment in staging.
4. Roll out to production behind a Waffle flag (`evaluations.auto_populate_datasets`) initially; remove the flag after a soak period.
5. Rollback: disable the Waffle flag (stops new rule creation and rule execution), or unschedule the beat task. Existing data is additive and can be reversed by deleting rules and the auto-created `EvaluationMessage` rows; their `DatasetIngestionEntry` rows make this audit-able.

## Open Questions

- Should `last_ingested_at` initialise to `now()` (forward-only) or to the rule's selected source experiment's earliest matching message (full backfill)? Current proposal: forward-only; user can run a manual import to backfill. Confirm with stakeholders before sealing.
- Is a 5-minute default poll interval the right starting point, or should it be configurable per rule? Lean: global default for simplicity, revisit if users ask.
- Do we want to allow a rule to write into a dataset whose `evaluation_mode` is `session` while pulling individual messages, or strictly require the modes match? Lean: strictly match; surface as a form validation error.
- Should auto-disable after N failures be N=3 or configurable? Lean: hard-coded 3, surfaced via a notification, revisit after operational data.
