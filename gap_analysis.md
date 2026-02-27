# Gap Analysis: n8n Chatbot Analytics Workflows vs OCS Evaluations

**Date:** 2026-02-27
**Author:** Gap analysis generated against OCS evaluations codebase
**Scope:** Two n8n analysis pipelines (Transcript Analysis, Engagement Analysis) vs the OCS evaluations feature

---

## 1. Executive Summary

The OCS evaluations feature and the n8n workflows address partially overlapping but fundamentally different concerns. OCS evaluations is a robust, developer-facing QA framework: it stores structured human→AI turn pairs, runs them through configurable LLM or Python evaluators, aggregates results per run, and exposes everything through a UI and CSV download. For the core quality-assessment use case — specifically accuracy classification of bot responses against a known Q&A set — OCS already provides the building blocks (`LlmEvaluator` with a custom prompt and `ChoiceFieldDefinition` output schema, `DistributionAggregator` for label-level percentages). A team that already has an OCS chatbot can replicate the n8n Accuracy Analysis with zero new development.

The Transcript Analysis pipeline introduces two capabilities that OCS partially supports but does not fully cover. First, coverage analysis requires a knowledge-base concept — a QnA bank that can be uploaded and associated with an evaluation config — which OCS does not have. A `PythonEvaluator` could implement the Jaccard similarity algorithm in user-supplied code, but there is no model, UI, or storage mechanism for attaching a QnA bank. Second, the pipeline translates multilingual user messages to English before analysis; OCS has no translation layer and no mechanism for pre-processing messages before they enter an evaluator.

The Engagement Analysis pipeline represents a true and complete gap. OCS evaluations has no concept of session-level analytics, temporal engagement metrics, or user retention analysis. The weekly activity summaries, per-session duration and sub-session metrics, monthly engagement tiers, and power-user concentration statistics that the n8n workflows produce have no equivalent anywhere in the OCS evaluations system. Closing this gap would require a new analytics subsystem — either a dedicated Django app or a substantial extension of the evaluations app — with session-level data models, a deterministic aggregation layer, and report delivery mechanisms such as email notifications or scheduled exports.

---

## 2. Capability Comparison Table

| Capability | n8n implementation | OCS evals support | Gap level |
|---|---|---|---|
| Dataset ingestion from external CSV/Excel transcript export | CSV/XLSX upload via form; raw message-row format (Session ID, Message Date, Message Type, Message Content) | CSV upload with column mapping; requires pre-formed input/output pairs; no XLSX support | **Partial** |
| Human→AI turn pair extraction | JavaScript groups raw message rows by session, stitches consecutive human turns into pairs | Requires pairs to be pre-formed in CSV; session cloning works for OCS-native sessions only | **Partial** |
| Multilingual support / translation before analysis | Google Translate API translates user messages to English before coverage matching | No translation support anywhere in the evaluations pipeline | **Full gap** |
| Coverage analysis (in-scope/out-of-scope classification against a knowledge base) | Jaccard similarity engine + rule-based pre-classification against an uploaded QnA bank; FLW-specific prefix stripping | No knowledge-base concept; `PythonEvaluator` could implement Jaccard logic but there is no QnA bank model, upload path, or config linkage | **Partial** |
| Accuracy analysis (LLM-based response quality classification) | GPT-4.1-nano with detailed system prompt; temperature=0; structured JSON output | `LlmEvaluator` supports custom prompt templates calling any configured LLM provider with structured output via Pydantic | **None** |
| Accuracy taxonomy (accurate / inaccurate / non_answer with subtypes) | Hardcoded in accuracy prompt; returns `accuracy_main`, `accuracy_subtype`, `rationale` | `ChoiceFieldDefinition` for categorical fields; `StringFieldDefinition` for rationale; all fully composable in `output_schema` | **None** |
| Custom classification prompt / system prompt authoring | System prompt hardcoded in n8n node; requires workflow-editor access to change | `LlmEvaluator.prompt` is a UI-editable text field; any authorized team member can modify | **None** |
| Structured output schema for LLM evaluators | JSON schema in OpenAI node with regex fallback parser | `output_schema: dict[str, FieldDefinition]` in `LlmEvaluator`; uses `with_structured_output(pydantic_model)` — no regex fallback needed | **None** |
| Filtering by bot type / user segment | Bot-type dropdown (FLW/Mother) drives FLW-specific prefix stripping in coverage analysis | `EvaluationMessage.participant_data` can store bot type, but no first-class evaluator parameter or UI filter for it | **Partial** |
| Aggregate KPIs (coverage %, accuracy %, conditional accuracy %) | Custom JS computes total pairs, matched %, non-question breakdown, accurate/(accurate+inaccurate), accurate/matched-questions | `DistributionAggregator` computes % per label; no cross-field derived metrics (e.g. conditional accuracy filtered to matched questions only) | **Partial** |
| Numeric and categorical aggregation | Custom JS; counts, percentages, derived ratios | `MeanAggregator`, `MedianAggregator`, `MinAggregator`, `MaxAggregator`, `StdDevAggregator`; `DistributionAggregator`, `ModeAggregator` — all auto-applied per field type | **None** |
| Weekly engagement metrics (users, sessions, messages, session minutes) | ISO-week bucketing; new vs returning user tracking; 30-min inactivity threshold for session duration; week-boundary split for cross-week sessions | No temporal or session-level analytics whatsoever | **Full gap** |
| Per-session metrics (duration, sub-session count) | Per-session: `session_duration_minutes`, `sub_sessions` (30-min gap threshold), `messages_per_minute` | No session-level aggregation; `EvaluationMessage` stores individual turn pairs, not session summaries | **Full gap** |
| User retention / engagement tiers | Monthly counts by number of active weeks (1, 2, 3, 4+); "core users" = 2+ weeks | No retention analytics | **Full gap** |
| Power-user concentration metrics | Top-10% users by session count per month; % of total sessions they account for | No such analytics | **Full gap** |
| Report delivery (email notification, Google Sheets export) | SMTP email with Google Sheet URL on completion; primary output is a multi-tab Google Sheet | No email notifications; CSV download from OCS UI; no Google Sheets integration | **Full gap** |
| Run management (async execution, preview runs, status tracking) | n8n worker; synchronous per-trigger; no preview concept | Celery chord (concurrency=10); `EvaluationRunType.PREVIEW` samples dataset; `EvaluationRunStatus` (PENDING/PROCESSING/COMPLETED/FAILED); progress tracking | **None** (OCS is superior) |
| Results downloadable as CSV | Google Sheets is primary output; intermediate CSV files as debug artifacts only | `download_evaluation_run_csv` view; full result set including evaluator columns downloadable | **None** |

---

## 3. Detailed Gap Analysis

### Gap 1: Raw Transcript Ingestion (XLSX + Message-Level Format)

**What the n8n workflow does:**
Accepts an XLSX or CSV transcript where each row is a single chat message (columns: `Session ID`, `Message Date`, `Message Type` [human/ai], `Message Content`, `Participant Public ID`). The `QnA Pairing` JavaScript node then groups these rows by session, sorts chronologically, and stitches consecutive human turns into human→AI pairs.

**What OCS currently supports:**
`EvaluationDataset` creation via `create_dataset_from_csv_task` expects the CSV to already contain one row per human→AI pair, with `input` and `output` columns (or custom-mapped equivalents). XLSX is not supported; only UTF-8 CSV is accepted by the task. Session cloning (`create_dataset_from_sessions_task`) builds pairs from OCS-native `ChatMessage` rows — that path works for data that originated in OCS, but not for externally exported transcripts.

**Gap description:**
Two sub-gaps: (a) XLSX files cannot be ingested at all; (b) raw message-level transcript exports (one row per message, not per pair) cannot be converted to turn pairs without external preprocessing.

**Suggested OCS approach:**
1. Add XLSX file support to `update_dataset_from_csv_task` and `create_dataset_from_csv_task` by detecting the file extension and using `openpyxl` (already a common Django dependency) to convert to rows before the existing CSV processing path.
2. Add a new dataset-creation mode `"raw_transcript"` to `EvaluationDatasetForm` that accepts a raw message-level export (configurable columns for session ID, timestamp, role, content) and runs the same pairing algorithm: sort by session + timestamp, stitch consecutive same-role turns, emit human→AI pairs as `EvaluationMessage` objects. This is essentially a Python port of the n8n `QnA Pairing` JS node.

**Effort estimate:** Medium. XLSX support is a one-day add-on. The raw transcript pairing mode requires new form fields, a new task path, and column-mapping UI, but the logic itself is straightforward (~100 lines of Python).

---

### Gap 2: Multilingual Pre-Processing / Translation

**What the n8n workflow does:**
Passes each pair's `human_text` (prepended with `pair_index`) through the Google Translate API to English before coverage analysis. The output (`human_text_en`, `human_lang`) is joined back onto the pair record and used for Jaccard matching against the English-language QnA bank. The original `human_text` is preserved for display.

**What OCS currently supports:**
Nothing. `EvaluationMessage.input` stores the message content verbatim. `LlmEvaluator` and `PythonEvaluator` receive the content as-is. There is no pre-processing step in the evaluation pipeline.

**Gap description:**
No translation layer exists, and there is no hook in the evaluation execution flow (`evaluate_single_message_task`) to run message content through an external API before evaluators fire.

**Suggested OCS approach:**
Add an optional `preprocessing` list to `EvaluationConfig` (or as a separate `PreprocessingStep` model). Initially implement a `TranslationPreprocessor` that calls an LLM provider's translation capability (to avoid a separate Google Translate credential requirement) or accepts a configurable target language. The pre-processed content would be stored in `EvaluationMessage.metadata` under a key like `translated_input`, and evaluator prompt templates would gain a `{translated_input}` variable alongside `{input.content}`.
Alternatively, add a dedicated `TranslationEvaluator` type that writes a `translated_content` field into `EvaluationResult.output`, and evaluators downstream in the same config can reference it. The simpler path is a pre-processing hook in `evaluate_single_message_task` before the evaluator loop.

**Effort estimate:** Medium. The execution model change (pre-processing hook) is ~1 day. Building a clean, configurable `TranslationPreprocessor` with UI and credential integration is another 2–3 days. The key complexity is deciding where translated content lives and how downstream evaluators reference it.

---

### Gap 3: Knowledge-Base / QnA Bank Integration for Coverage Analysis

**What the n8n workflow does:**
Accepts a second file upload (QnA bank) alongside the transcript. Each row has at minimum a `question` and `qna_id`. The `Coverage Analysis: JS` node loads all QnA rows, computes Jaccard similarity between each user message (English) and every QnA question, and classifies the turn as `Matched question` / `Unmatched question` / `Non-question`. The bot-type field drives FLW-specific phrasing normalisation.

**What OCS currently supports:**
There is no `KnowledgeBase` or `QnABank` model in the evaluations app. A `PythonEvaluator` could implement the Jaccard matching algorithm in sandboxed Python code, but the QnA bank data has nowhere to live — it cannot be referenced from within the evaluator's code sandbox, because the sandbox receives only the per-message `input`, `output`, `context`, `full_history`, and `generated_response` variables.

**Gap description:**
Two structural gaps: (a) no model for storing a QnA bank (set of question/answer pairs) and associating it with an evaluation config; (b) no mechanism for passing dataset-level reference data (the QnA bank) into evaluator execution.

**Suggested OCS approach:**
1. Add a `KnowledgeBase` model (team-scoped, `BaseTeamModel`) with `name`, `content` (JSONField storing list of `{id, question, answer}` dicts), and an optional CSV upload path in the UI.
2. Add an optional `knowledge_base` FK from `EvaluationConfig` to `KnowledgeBase`.
3. Extend `evaluate_single_message_task` and the `PythonEvaluator`/`LlmEvaluator` run interface to accept an optional `knowledge_base` argument (passed as a Python list of dicts in the sandbox's global scope, or as a template variable `{knowledge_base}` in LLM prompts).
4. Ship a built-in `CoverageEvaluator` that implements the Jaccard algorithm with configurable threshold, stopword list, and bot-type normalisation rules — this gives teams a first-class coverage metric without writing Python code.

**Effort estimate:** High. The data model and UI for `KnowledgeBase` is ~1–2 days. Threading `knowledge_base` through the evaluator execution stack and sandbox safely requires careful design (~2 days). The `CoverageEvaluator` itself is ~1 day of Python. Total ~5–7 days.

---

### Gap 4: Derived / Cross-Field Aggregate KPIs

**What the n8n workflow does:**
The `Overall C+A Summary generator` JavaScript node computes two derived KPIs that require cross-field logic:
- **Primary KPI (conditional accuracy):** `accurate / (accurate + inaccurate)` — excludes `non_answer` from the denominator, capturing "when the bot attempts an answer, is it correct?"
- **Secondary KPI:** `accurate / total matched questions` — the fraction of in-scope questions that received a correct answer.
Both require counting subsets of rows (e.g. only `Matched question` rows, excluding `non_answer` rows from a denominator), which is not a simple per-field aggregation.

**What OCS currently supports:**
`EvaluationRunAggregate` is populated by `compute_aggregates_for_run` → `compute_evaluator_aggregates`. The `DistributionAggregator` computes the percentage breakdown of each categorical label within a single field. There is no mechanism for computing a ratio across two fields, or for computing an aggregation conditional on a filter (e.g. "only for rows where `coverage_main == Matched question`").

**Gap description:**
The aggregation system is strictly per-field and unconditional. Derived cross-field metrics (conditional accuracy, subset-filtered percentages) cannot be expressed in the current `BaseAggregator` interface.

**Suggested OCS approach:**
Add a `DerivedMetricAggregator` concept to `apps/evaluations/aggregators.py`. A derived metric is defined as a formula over other aggregated fields (e.g. `numerator_label_count / (numerator_label_count + denominator_label_count)`). This could be implemented as:
1. A new `DerivedFieldDefinition` in `field_definitions.py` with a `formula` expression (referencing other output_schema field names and label values).
2. A post-processing pass in `compute_evaluator_aggregates` that evaluates these formulas after primary aggregation is complete.
Alternatively, allow `PythonEvaluator` to emit multiple fields including a pre-computed conditional ratio (e.g. the evaluator function itself checks if `coverage_main == "Matched question"` and emits a boolean `is_matched`, which combined with float aggregation over a `correct_if_matched` field gives the conditional accuracy).

**Effort estimate:** Medium. A clean formula-based `DerivedMetricAggregator` requires careful API design but the implementation is ~2 days. The workaround (multi-field Python evaluator) requires no framework changes.

---

### Gap 5: Bot-Type / Segment Parameterisation

**What the n8n workflow does:**
The user selects "FLW" or "Mother" from a dropdown when submitting the form. This value is forwarded to the coverage analysis engine, which applies FLW-specific prefix stripping (e.g. removing "If a mother asks me...", "How do I explain..." prefixes) before Jaccard matching. Different bot types have different phrasings for the same underlying question.

**What OCS currently supports:**
`EvaluationConfig` has no bot-type or segment parameter. `EvaluationMessage.participant_data` can store arbitrary participant metadata (including bot type if the source data includes it), and `EvaluationMessage.session_state` can store session-level state. These are available to `PythonEvaluator` via the `context` or could be passed differently, but there is no standardised mechanism for a single evaluation config to vary its behaviour based on a categorical run-time parameter.

**Gap description:**
No mechanism to pass a run-level categorical parameter (e.g. bot type) into evaluators, and no UI to select such a parameter when creating a run.

**Suggested OCS approach:**
Add a `run_params: JSONField` to `EvaluationConfig` (or to `EvaluationRun` for per-run overrides). Expose these params as an additional keyword-argument dict (`**run_params`) in the `PythonEvaluator`'s `main()` function signature and as a `{run_params.[key]}` template variable in `LlmEvaluator` prompts. The UI for `EvaluationConfig` would allow defining key-value pairs (similar to how pipeline node parameters work). This generalises bot-type filtering into a reusable parameter injection mechanism.

**Effort estimate:** Low–Medium. The data model change is trivial. Threading `run_params` through `evaluate_single_message_task` into the evaluator `run()` interface is ~1 day. UI for defining and overriding params is ~1 day.

---

### Gap 6: Engagement / Session Analytics

**What the n8n workflow does:**
Three parallel JavaScript generators produce:
1. **Weekly Activity Summary:** Per ISO-week: active/new/returning users, session count, message counts, total and average session minutes (30-min inactivity threshold; cross-week gap splitting).
2. **Weekly Session Summary:** Per session: start/end timestamps, duration, sub-session count, messages-per-minute.
3. **User Engagement/Retention:** Per month: engagement tiers (1/2/3/4+ active weeks), core users (≥2 weeks), top-10% power-user session concentration, per-user lifetime summary, per-user monthly drilldown.

**What OCS currently supports:**
The evaluations app has no temporal or engagement analytics. `ExperimentSession` stores session metadata (participant, experiment, channel, created_at, last message timestamp) and `ChatMessage` stores individual messages with timestamps — the raw data exists in OCS. However, there is no analytics layer that aggregates across sessions over time.

**Gap description:**
An entirely new analytics subsystem is required. The data is present in OCS (sessions and messages with timestamps), but there is no model, task, view, or UI for the engagement and retention metrics the n8n workflows produce.

**Suggested OCS approach:**
Add a `SessionAnalyticsReport` feature (possibly a new Django app `apps/analytics` or an extension of `apps/evaluations`) with:
1. A `SessionAnalyticsConfig` model linking to a team and a chatbot (and optionally an `EvaluationDataset` or direct session queryset filter).
2. A Celery task `generate_session_analytics_task` that queries `ExperimentSession` and `ChatMessage`, applies the same algorithmic logic as the n8n JS generators (ISO week bucketing, 30-min inactivity threshold, session duration, sub-session counting), and stores results in `SessionAnalyticsResult` rows.
3. A `WeeklyActivitySummary` and `UserEngagementSummary` model (or JSON aggregates stored on the task result) to hold the computed metrics.
4. A read-only UI page and CSV download.
The engagement generators are pure deterministic aggregations — no LLM calls — so they are cheap to run and can be scheduled (e.g. weekly Celery beat task) or triggered on-demand.

**Effort estimate:** High. This is a substantial new subsystem. The analytics logic itself (porting the JS generators to Python) is ~3 days. Data models, Celery task, API views, and UI are another 4–5 days. Scheduling and monitoring adds another day. Total ~8–10 days.

---

### Gap 7: Report Delivery (Email Notification + Scheduled Export)

**What the n8n workflow does:**
After all sheet tabs are written, an SMTP email is sent to the address supplied at form submission. The email body contains the Google Sheet URL. This closes the feedback loop for non-technical users who triggered the analysis.

**What OCS currently supports:**
No notification system in the evaluations feature. Users must poll the OCS UI or observe the `EvaluationRunStatus` field. `EvaluationRun` has `finished_at` and `status` fields that could be used to trigger notifications, but no task or signal currently does so.

**Gap description:**
No delivery mechanism. Results exist in the OCS database and can be downloaded as CSV, but there is no automated delivery to a recipient when a run completes.

**Suggested OCS approach:**
1. Add a `notification_email` optional field to `EvaluationConfig` (or allow it to be entered per-run in the run-creation UI).
2. In `mark_evaluation_complete`, after `compute_aggregates_for_run`, if a `notification_email` is set, dispatch a `send_evaluation_complete_email_task` Celery task. The email body should include run status, aggregate KPIs, and a direct link to the OCS results page.
OCS already has email infrastructure (Django email backend, `settings.EMAIL_*`), so this is an integration exercise rather than new infrastructure. A Google Sheets export is lower priority — a direct link to the OCS results page (already a URL in `EvaluationRun.get_absolute_url()`) or the CSV download URL may be sufficient.

**Effort estimate:** Low. Email sending logic is ~0.5 day. The notification_email field, form UI, and task wiring is another 0.5 day. Total ~1 day.

---

## 4. Items OCS Does Better (or Differently)

The following areas represent capabilities where OCS's architecture is strictly more capable or principled than the n8n workflows:

1. **Structured LLM output reliability.** `LlmEvaluator` uses LangChain's `with_structured_output(pydantic_model)` to force the LLM into a validated JSON schema. The n8n Accuracy Analysis node has a two-path parser with a regex fallback because the OpenAI API occasionally returns malformed JSON. OCS's approach eliminates this class of parse failure entirely.

2. **Evaluator reusability.** An `Evaluator` object is a reusable, named configuration that can be attached to multiple `EvaluationConfig` instances. In n8n, the accuracy prompt is embedded in a single workflow node — changing it requires workflow-editor access and creates no audit trail. In OCS, evaluator edits are tracked through the Django admin/audit system.

3. **Preview runs.** `EvaluationRunType.PREVIEW` runs evaluators on a small sample (`PREVIEW_SAMPLE_SIZE` messages) before committing to a full run. This allows prompt iteration and sanity-checking without incurring full LLM API costs. The n8n workflow has no equivalent (a disabled `Filter: 20` node was the development-time workaround).

4. **Result overwrite via CSV.** `upload_evaluation_run_results_task` allows a human reviewer to download evaluation results as CSV, override specific evaluator judgements (e.g. correct an LLM misclassification), re-upload, and have aggregates recomputed. The n8n workflow produces a static Google Sheet — corrections require manual in-sheet editing with no propagation back to computed KPIs.

5. **Versioned chatbot generation.** `EvaluationConfig.experiment_version` links to a specific chatbot version (or `LATEST_WORKING`/`LATEST_PUBLISHED` sentinel values). When `run_generation` is enabled, each evaluation message is sent through the live chatbot to obtain a fresh `generated_response` for comparison against the ground-truth `output`. The n8n workflow evaluates static transcript exports only — it cannot test a new bot version against historical questions.

6. **Multi-tenancy and access control.** All evaluation objects are scoped to a `Team` via `BaseTeamModel`, and views are protected by Django permissions (`evaluations.view_evaluationdataset`, etc.). The n8n workflow is accessible to anyone who has the n8n form URL.

7. **Concurrent per-message evaluation.** `run_evaluation_task` dispatches evaluation as a Celery chord with configurable concurrency (default 10 parallel workers). The n8n workflow processes messages sequentially within a single execution (the OpenAI accuracy node is called one item at a time). At scale, OCS is significantly faster.

---

## 5. Recommended Priority Order

The following ranking weighs user impact (does closing this gap unlock the workflows for OCS users?), implementation complexity, and whether the gap blocks downstream use cases.

| Priority | Gap | Justification |
|---|---|---|
| 1 | **Gap 6: Engagement / Session Analytics** | The entire Engagement Analysis pipeline is a full gap with zero OCS support. This is the most-used output of the n8n system (teams run the Engagement Pipeline independently). Closes the largest functional delta. |
| 2 | **Gap 3: Knowledge-Base / QnA Bank Integration** | Coverage analysis is the first half of the Transcript Analysis pipeline and cannot be replicated in OCS today. Enabling this unblocks users from migrating away from n8n entirely. Also required before Gap 4 (conditional coverage KPIs) is meaningful. |
| 3 | **Gap 1: Raw Transcript Ingestion (XLSX + Message-Level Format)** | Without this, external transcript data cannot enter OCS at all for teams that export raw message logs (not pre-formed pairs) or in XLSX format. Blocks all n8n workflow migration for those teams. XLSX support alone is a quick win. |
| 4 | **Gap 4: Derived / Cross-Field Aggregate KPIs** | Coverage % and conditional accuracy % are the headline KPIs stakeholders see in the Overall Analysis Summary tab. Once coverage analysis (Gap 3) is in OCS, the inability to compute `accurate / (accurate + inaccurate)` is an immediately visible limitation. |
| 5 | **Gap 7: Report Delivery (Email Notification)** | Non-technical users who trigger analyses expect an automated delivery to their inbox. Without this, they must poll the OCS UI. High user-experience impact; very low implementation effort (~1 day). |
| 6 | **Gap 5: Bot-Type / Segment Parameterisation** | Required to replicate FLW-specific normalisation in coverage analysis. Low standalone value until Gap 3 (KnowledgeBase) is implemented; correct prioritisation is: implement Gap 3 first, then Gap 5 as a follow-on to make coverage analysis production-accurate for FLW datasets. |
| 7 | **Gap 2: Multilingual Pre-Processing / Translation** | Required for deployments serving non-English users. Medium complexity (external API or LLM-based translation). Lower priority than the structural gaps above because teams can pre-translate their datasets externally before CSV upload as a workaround; this gap does not block OCS adoption, it just creates an extra step. |
