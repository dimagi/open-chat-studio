## Why

Today, evaluation datasets must be populated manually: a user picks sessions or applies filters in the UI, then triggers a one-shot import. As the chatbot continues to receive traffic, the dataset goes stale unless someone remembers to refresh it and re-run linked evaluations. Teams that want continuous evaluation against fresh production traffic have no way to express "every new session that matches X should flow into dataset Y, and evaluation Z should run on the new rows."

## What Changes

- Introduce **dataset auto-population rules**: a team-scoped configuration on an `EvaluationDataset` that specifies (a) a source experiment, (b) selection criteria reusing the existing `ChatMessageFilter` / `ExperimentSessionFilter` system, and (c) the dataset evaluation mode (message vs. session) it ingests for.
- Add a periodic ingestion task that walks each enabled rule, finds sessions/messages newer than the rule's high-water mark, builds `EvaluationMessage` rows for them, and appends them to the linked dataset — without re-importing previously ingested rows.
- Track ingestion provenance on `EvaluationMessage` (or a join table) so the same source session/message is never appended twice and so administrators can see *which* rule introduced a row.
- Allow an `EvaluationConfig` to be marked **auto-run on dataset append**. When auto-population adds new messages to its dataset, a delta `EvaluationRun` is enqueued automatically, evaluating *only the newly added rows* rather than the full dataset.
- Surface auto-population state and recent ingestion runs in the dataset UI (status, last run, count appended, errors).
- **BREAKING**: none. All existing manual workflows continue to function; auto-population is opt-in per dataset.

## Capabilities

### New Capabilities

- `dataset-auto-population`: Rule-driven, scheduled ingestion of new sessions/messages from a source experiment into an evaluation dataset, including filter-based selection, deduplication against prior ingestion, and operator-visible status.
- `dataset-linked-evaluations`: Automatic enqueuing of an evaluation run scoped to newly appended dataset rows whenever auto-population (or another append path) adds messages, including configuration on `EvaluationConfig` to opt in and a delta-only run mode.

### Modified Capabilities

<!-- No existing specs in openspec/specs/ — first capabilities for this project. -->

## Impact

- **Apps touched**: `apps/evaluations` (models, tasks, forms, views, migrations, admin, tables), `apps/experiments` (read-only consumer of filters), `apps/web/dynamic_filters` (reuse, no changes expected).
- **New models / fields**: a `DatasetAutoPopulationRule` model; new flags on `EvaluationConfig` (auto-run-on-append) and possibly on `EvaluationRun` (delta-vs-full marker, scoped messages); provenance tracking on `EvaluationMessage` or a join table.
- **Background jobs**: a new periodic Celery task (Celery Beat) for ingestion; reuse of existing `run_evaluation_task` with a delta-message scope.
- **APIs / UI**: new dataset rule management views and forms; status/last-ingestion display in dataset detail; opt-in toggle on evaluation config form.
- **Migrations**: schema additions only; backwards-compatible. Manual dataset workflows remain default behaviour.
- **Operational**: introduces ongoing background work proportional to rule count × poll frequency; ingestion must be idempotent and team-scoped.
