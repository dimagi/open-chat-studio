## ADDED Requirements

### Requirement: Auto-population rule definition

A team SHALL be able to define one or more **auto-population rules** on an `EvaluationDataset`. Each rule MUST specify a source experiment, an evaluation mode (`message` or `session`) that matches the dataset's mode, persisted filter criteria using the existing `ChatMessageFilter` / `ExperimentSessionFilter` query-string format, and an enabled flag. A rule MUST be team-scoped via `BaseTeamModel` and MUST NOT reference resources outside its team.

#### Scenario: Rule created with matching evaluation mode

- **WHEN** a user submits a new rule whose mode matches the target dataset's `evaluation_mode` and whose source experiment belongs to the user's team
- **THEN** the rule is persisted with `is_enabled=True`, `last_ingested_at=now()`, and `last_run_status=null`

#### Scenario: Rule rejected when evaluation modes mismatch

- **WHEN** a user submits a rule whose `evaluation_mode` differs from the target dataset's `evaluation_mode`
- **THEN** the form returns a validation error and no rule is persisted

#### Scenario: Rule rejected when source experiment is from another team

- **WHEN** a user submits a rule whose `source_experiment` belongs to a different team than the rule's `team`
- **THEN** the form returns a validation error and no rule is persisted

### Requirement: Periodic ingestion task

The system SHALL register a Celery Beat periodic task that, on each tick, processes every enabled `DatasetAutoPopulationRule`. For each rule the task MUST query its source experiment for chat messages (or sessions) created after `last_ingested_at - safety_margin`, exclude any source already recorded in `DatasetIngestionEntry` for that rule, build `EvaluationMessage` rows via the existing `make_evaluation_messages_from_sessions` helper, append them to the rule's dataset, write one `DatasetIngestionEntry` per source, and bump `last_ingested_at` to the maximum source `created_at` of the batch.

#### Scenario: New matching messages are ingested

- **GIVEN** a rule whose source experiment has new chat messages matching its filter and dataset evaluation_mode `message`
- **WHEN** the periodic task runs
- **THEN** new `EvaluationMessage` rows are created, attached to the dataset, the rule's `last_ingested_at` is advanced, `last_run_status` is `success`, and a `DatasetIngestionEntry` exists for each appended source `ChatMessage`

#### Scenario: Already-ingested message is not re-appended

- **GIVEN** a rule that previously appended `ChatMessage` X to its dataset (a `DatasetIngestionEntry` exists for `(rule, X)`) and X still matches the rule's filter
- **WHEN** the periodic task runs again and X falls within the lookback window
- **THEN** no new `EvaluationMessage` is created for X and the dataset's membership is unchanged for that source

#### Scenario: Rule with no new matching data is a no-op

- **WHEN** the periodic task runs against a rule whose source experiment has produced no new messages since `last_ingested_at - safety_margin`
- **THEN** the dataset's membership is unchanged, `last_run_status` is `no_op`, and `last_ingested_at` is not changed

### Requirement: Idempotent ingestion under concurrency

The ingestion task SHALL acquire a row-level lock on the rule (e.g., `select_for_update(skip_locked=True)`) before processing it, so that two beat workers running concurrently do not both ingest the same window. The unique constraint on `DatasetIngestionEntry` MUST be `(rule, source_message_id)` for message-mode rules and `(rule, source_session_id)` for session-mode rules, providing a database-level safety net against double-insert.

#### Scenario: Concurrent workers do not double-ingest

- **GIVEN** two Celery workers picking up the same rule simultaneously
- **WHEN** both attempt to process it
- **THEN** exactly one worker performs the work, the other skips that rule for this tick, and the dataset receives each matching source exactly once

#### Scenario: Crash mid-batch does not cause duplicates

- **GIVEN** a rule whose ingestion task crashed after creating some `DatasetIngestionEntry` rows but before bumping `last_ingested_at`
- **WHEN** the next tick re-processes the rule
- **THEN** sources already recorded in `DatasetIngestionEntry` are skipped and the dataset receives each source exactly once across the two attempts

### Requirement: Failure handling and auto-disable

When ingestion fails for a rule, the rule MUST record `last_run_status="error"` and `last_error=<message>` without affecting other rules in the same beat tick. After three consecutive failed runs the rule MUST be set to `is_enabled=False` and an admin notification MUST be raised via `apps.ocs_notifications`.

#### Scenario: One bad rule does not block other rules

- **GIVEN** rules A, B, and C are enabled and rule B raises an exception during ingestion
- **WHEN** the periodic task runs
- **THEN** rules A and C are processed normally, rule B records its error, and a single tick completes without aborting

#### Scenario: Rule auto-disables after three consecutive failures

- **GIVEN** a rule that has just failed for the third consecutive time
- **WHEN** the failure is recorded
- **THEN** the rule's `is_enabled` is set to `False` and a notification is created for team administrators

### Requirement: Forward-only ingestion start point

A newly created rule SHALL initialise `last_ingested_at` to its `created_at` so that the first ingestion tick only considers chat messages produced *after* the rule existed. Backfilling historical data MUST be performed via the existing manual filter-import workflow, not via auto-population rules.

#### Scenario: New rule does not import history

- **GIVEN** a source experiment with chat messages predating the rule's creation
- **WHEN** the rule is created and the next periodic tick runs
- **THEN** none of the pre-existing chat messages are appended to the dataset, even if they match the rule's filter

### Requirement: Status visibility on dataset detail

The dataset detail UI SHALL display, for each rule, its source experiment, evaluation mode, enabled state, last run timestamp, last run status, last error (if any), and a count of messages contributed by the rule (derivable from `DatasetIngestionEntry`).

#### Scenario: Operator views rule status

- **WHEN** an operator opens the dataset detail page for a dataset with one or more rules
- **THEN** the page renders the listed status fields for each rule and links to a rule edit form
