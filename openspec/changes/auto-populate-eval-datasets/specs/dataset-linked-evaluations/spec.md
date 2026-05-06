## ADDED Requirements

### Requirement: Opt-in auto-run on append

An `EvaluationConfig` SHALL expose a boolean `auto_run_on_append` (default `False`) that controls whether the system automatically enqueues an evaluation run when its dataset receives newly appended messages. The flag MUST be editable in the existing evaluation config form.

#### Scenario: Toggling auto-run persists

- **WHEN** a user enables `auto_run_on_append` on an evaluation config and saves the form
- **THEN** the field is persisted and subsequent dataset append events trigger runs for this config

#### Scenario: Default is opt-in off

- **WHEN** a new `EvaluationConfig` is created without explicitly setting `auto_run_on_append`
- **THEN** the field defaults to `False` and no automatic runs occur

### Requirement: Delta evaluation runs

The system SHALL support a new `EvaluationRunType.DELTA` that evaluates only an explicit subset of an `EvaluationConfig`'s dataset rather than the full dataset. A delta run MUST persist the in-scope `EvaluationMessage` IDs (e.g., on an `EvaluationRun.scoped_messages` M2M) at enqueue time and MUST evaluate only those messages, even if more messages are appended to the dataset before the run completes.

#### Scenario: Delta run evaluates only scoped messages

- **GIVEN** a dataset containing 100 `EvaluationMessage` rows and a delta run scoped to 5 specific message IDs
- **WHEN** the run executes
- **THEN** exactly 5 `EvaluationResult` rows are produced (one per evaluator per scoped message), and the other 95 dataset messages are not evaluated by this run

#### Scenario: Delta run is unaffected by concurrent appends

- **GIVEN** a delta run is enqueued with a scope of 5 specific messages
- **WHEN** an additional 10 messages are appended to the dataset before the delta run executes
- **THEN** the run still evaluates only the original 5 scoped messages

### Requirement: Auto-trigger on append

When new `EvaluationMessage` rows are appended to a dataset (regardless of source — auto-population, manual import, or future paths), the system SHALL find every `EvaluationConfig` whose `dataset` is that dataset and whose `auto_run_on_append` is `True`, and enqueue one `EvaluationRunType.DELTA` run per matching config with the newly appended messages as its scope. Tagging behaviour for delta runs MUST follow the same rules as `FULL` runs (i.e., evaluator tag rules apply; preview-only suppression does not apply).

#### Scenario: Auto-population triggers a delta run

- **GIVEN** a dataset with two `EvaluationConfig`s, one with `auto_run_on_append=True` and one with `False`
- **WHEN** the auto-population task appends 3 new messages to the dataset
- **THEN** exactly one new `EvaluationRun` is created (for the opted-in config), with `type=DELTA` and a scope of those 3 messages

#### Scenario: No append, no run

- **WHEN** the auto-population task processes a rule that ingests no new messages
- **THEN** no `EvaluationRun` is created for any linked configs

#### Scenario: Manual append also triggers auto-run

- **GIVEN** a manual filter-import that appends new messages to a dataset and a linked config with `auto_run_on_append=True`
- **WHEN** the import completes
- **THEN** a delta run is enqueued for the newly appended messages

### Requirement: Delta runs are visible in evaluation history

Delta runs SHALL appear in the same evaluation history view as full and preview runs, distinguishable by their `type` and clearly labelled as delta runs in the UI. The history view MUST show the count of messages in scope and the time the run was triggered.

#### Scenario: Operator sees delta runs alongside full runs

- **GIVEN** an evaluation config with a mix of full, preview, and delta runs
- **WHEN** the operator opens the run history page
- **THEN** all run types are listed, each labelled with its type and scope size, and delta runs link to a results page that shows results only for the scoped messages
