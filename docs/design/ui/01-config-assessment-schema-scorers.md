# 01 — Config: Assessment, Schema, and Scorers

> The user-facing configuration surface for the unified assessment system. Covers everything a Bot Builder or Team Lead sets up *before* an Assessment starts producing Scores: the Assessment list and detail shell, the creation/edit flow, the AssessmentSchema catalogue and editor, and the AutomatedScorer / HumanScorer configuration forms.
>
> **Anchored to** [`../unified-assessment.md`](../unified-assessment.md): D-3 (bot_version moves to run), D-7 (two prior-score visibility knobs), D-8 (schema clone-and-repoint), D-10 (shared schema with per-scorer output_fields), D-11 (IRR sampling).
>
> **Out of scope** — covered by other briefs: Source configuration and RoutingRule builder (brief 02), review workflow (brief 03), runs/trends/concordance (brief 04).

## User stories addressed

This cluster of screens is the *configuration* entry point for every story. Specifically it lets a user express:

- Story 1, 3, 4 — set up one or more `AutomatedScorer`s on an Assessment.
- Story 5, 8, 9 — set up a `HumanScorer` with assignees, review count, and IRR sampling.
- Story 2 — mix automated and human scorers on one Assessment (calibration).
- Story 7 — point multiple scorers at the same `AssessmentSchema` so concordance has a real join key.

The Source-driven population semantics for Stories 4–6 are configured here (the "Source" sub-row on Assessment) but the deep config of filters/sample-rate/routing lives in brief 02.

## Information architecture

```
Assessments (list, list-of-lists)
├── New Assessment                  (creation flow / wizard)
└── /<assessment_id>/               (detail shell with tabs)
    ├── Overview     ← THIS BRIEF (config summary, edit affordances)
    ├── Source       ← brief 02
    ├── Scorers      ← THIS BRIEF (per-scorer add/edit)
    ├── Routing      ← brief 02
    ├── Runs         ← brief 04
    ├── Trends       ← brief 04
    ├── Concordance  ← brief 04
    └── Reviews      ← brief 03 (only when Assessment has a HumanScorer)

Schemas (parallel top-level navigation entry — catalogue)
├── New schema
└── /<schema_id>/                   (detail / edit)
```

**Why a separate top-level "Schemas" entry**: a schema is reusable across Assessments (FR-1.5). Surfacing it only inside an Assessment's edit form hides the catalogue and makes reuse invisible. The detail page also needs to show "used by N Assessments" and the schema-version history chain (D-8).

**Why the Assessment is one detail page with tabs, not a multi-page wizard after creation**: matches the pipeline-builder pattern already in the codebase (`templates/pipelines/`) and lets a user see config + activity for one Assessment without context-switching. Creation is a stepped flow; editing is per-tab.

## Screens

### S1 — Assessments list

**Replaces**: `templates/evaluations/evaluation_runs_home.html` (evals home) **and** `templates/human_annotations/queue_detail.html` (queue list). One list, one mental model.

**Purpose**: discover existing Assessments, see at-a-glance health, jump into the right one.

**Primary user**: Bot Builder, Bot Owner, Team Lead.

**Information shown per row**:
- Name + description (truncated)
- **Mode badge** — *Batch* (no `Source.filter_query_string`) or *Continuous* (filter set). Drives which other columns are meaningful.
- **Source summary** — dataset name (batch) or filter summary (continuous). One-line; long values truncated with tooltip.
- **Scorer mix chip** — small icons indicating which scorer kinds are present (LLM-judge, Python, Human). Tooltip shows count of each.
- **Last activity** — for batch: last `AssessmentRun.finished_at` + status pill. For continuous: most-recent `Score.created_at` + per-24h score count.
- **Schema name** — clickable; deep-link to schema detail.
- **Archived** state (filtered out by default).

**Primary actions**:
- "New Assessment" button (top-right).
- Row click → Assessment detail (Overview tab).
- Row menu: archive, duplicate, delete (delete only if no Scores reference it — otherwise archive-only).

**States**:
- *Empty*: friendly call-to-action ("No Assessments yet — assess your first signal"), single CTA, link to docs.
- *Loading*: standard table skeleton.
- *Populated*: paginated table.
- *Filter applied*: filter chips above the table.

**Filters / sort**: by mode (batch / continuous / both), by scorer kind, by schema, by archived state. Default sort: most-recent activity.

**Components**: `table`, `badge`, `btn`, `breadcrumbs`. Mode badge uses the same `badge-info` / `badge-accent` distinction as the trace-mode badge in existing experiment templates.

### S2 — New Assessment (creation flow)

**Replaces**: `templates/evaluations/evaluation_config_form.html` (eval config form) + `templates/human_annotations/queue_form.html` (queue form). Today these are two separate forms with overlapping concepts; the unified creation is one stepped flow.

**Purpose**: walk a user through the smallest valid Assessment setup, with sensible defaults so simple cases stay one-form-deep.

**Primary user**: Bot Builder, Team Lead.

**Shape**: stepped form (DaisyUI `steps` component). Each step is a thin slice; the user can move forward only when the current step validates. "Back" preserves state.

**Steps**:

1. **Basics**
   - Name (required), description (markdown allowed).
   - Mode toggle: *Batch* (default) ⇄ *Continuous*. Tooltip on each describing the difference. Selection determines what step 2 looks like.

2. **Source** *(thin; deep config in brief 02 lives on the detail page)*
   - **Batch mode**: pick an existing dataset (autocomplete) **or** create one inline (CSV / sessions / manual).
   - **Continuous mode**: minimal — granularity (`session` / `message`), filter string (with a "Copy from saved filter" affordance per D-12), optional sample rate.
   - "Configure full source later" link → finish step 2 with defaults, jumps to detail-page Source tab after save.

3. **Schema**
   - Pick existing `AssessmentSchema` (autocomplete with preview), **or** create new schema inline (opens schema editor mini-form — S6).
   - Show field summary once picked (name, count, types).

4. **Scorers**
   - "Add a scorer" — at least one required to save. Picker: `LLM Judge`, `Python`, `Human review`.
   - Each scorer opens the scorer editor (S5/S5b) as a modal/drawer; saved scorers stack vertically below the picker.
   - Each saved scorer shows: kind, name, `output_fields` chips, edit/remove.
   - Validation: at least one scorer is required. Mixed scorer kinds allowed (per D-1).

5. **Review & Save**
   - One-page summary of all steps with edit-back links.
   - "Save as draft" vs "Save and start" (batch only — continuous Assessments save and immediately begin listening).

**States**:
- *Draft persistence*: each step's state saved client-side as the user navigates; submit on final step.
- *Validation*: per-step inline, summarised on review step.
- *Schema-and-scorer interplay*: if user picks an existing schema and then adds a scorer with `output_fields` referencing fields not in the schema, block with inline error.

**Components**: `steps`, `card`, `modal`, `drawer`, `form-control`, `select`, `radio`, `btn`, `alert`.

### S3 — Assessment detail shell (Overview tab)

**Purpose**: a stable home for one Assessment. Tabs along the top, breadcrumbs, primary metadata. Other tabs are owned by other briefs; this brief defines only the shell + Overview content.

**Tabs** (left to right): Overview · Source · Scorers · Routing · Runs (batch only) · Trends (continuous only) · Concordance (≥2 scorer types) · Reviews (has HumanScorer). Tabs that don't apply to the Assessment are hidden, not greyed out.

**Overview content**:
- Header: name, description, mode badge, schema chip, archived state.
- "What this Assessment does" panel — auto-generated one-liner: *"Continuously scores sessions matching `<filter summary>` with `<scorer mix>` against schema `<name>`."* Updates with config changes; helpful for non-technical reviewers.
- **Scorer summary card** — each scorer as a row: kind icon + name + `output_fields` chips + status (active/disabled) + edit link.
- **Routing summary card** — count of routing rules, top-level glance ("3 rules: 1 emit-tag, 2 escalate-to-human"). Detail in brief 02.
- **Health/recency card** — for batch: latest 3 runs (status pill, started, finished, total scores). For continuous: latest score timestamp, 24h score count, count of `AppliedSourceFilter` failures in last 24h (per D-15 audit).
- **Activity timeline** (collapsed by default) — last 10 events: run started/finished, scorer added, schema repointed (D-8), config edited.

**Primary actions** (header right):
- Edit (opens the relevant step of the creation flow for non-destructive edits, or directly the field for in-place edits).
- "Run now" (batch only) — opens the run starter (brief 04).
- Pause / resume (continuous only) — toggle whether lifecycle hooks fire for this Assessment.
- Archive.
- Duplicate.

**Components**: `tabs`, `breadcrumbs`, `card`, `badge`, `stats`, `btn`, `dropdown` for the actions menu.

### S4 — Schema catalogue (list) and detail

**Replaces**: nothing — net new. Today schemas are inline JSON on `Evaluator.params["output_schema"]` and `AnnotationQueue.schema`. D-10 promotes them to a top-level catalogue.

#### S4a — Schema list

**Information per row**:
- Name + description
- Field count + type mix (e.g. "3 numeric, 2 categorical, 1 string")
- Used-by count (Assessments referencing this schema)
- Created date + author
- "Has successors" indicator (chain of clone-and-repointed schemas per D-8)

**Actions**: New schema · row click → detail · archive disabled (catalogue is append-only per D-8).

#### S4b — Schema detail / editor

**Two distinct modes**:

1. **Read-only view** when the schema has any `Score` referencing it. This is the normal case (D-8 — schemas are logically immutable once Scores reference them).
2. **Editable** when no Scores reference it yet (fresh schemas).

**Read-only view shows**:
- Field list with full definitions.
- "Used by" list — Assessments + scorers, with deep links.
- **Successor chain** (D-8) — if this schema has been cloned-and-repointed, show the chain visually:
  ```
  v1 (this) ─── v2 ─── v3 (current)
  Show this when |chain| > 1.
  ```
- **"Edit" button → triggers clone-and-repoint** (S4c).

**Field-level UI** (used in both modes):
- Type-aware editor:
  - `String`: max length, regex pattern.
  - `Int` / `Float`: min, max, step.
  - `Choice`: ordered list of options (drag-handle), allow-other toggle.
- Flags: `required`.
- Aggregation behaviour is **type-driven, not per-field-flagged**: `Numeric`, `Choice`, and `Boolean` fields always aggregate; `String` fields never do. No UI control needed (per FR-6.3).
- Field name — kebab-case, validated against system-reserved names (D-4: `user_thumb`, `user_feedback_reason`, etc.).

**Components**: `table`, `form-control`, `select`, `input`, `toggle`, `card`, `badge`. Field-list reordering uses the existing drag pattern from the pipeline builder.

#### S4c — Schema edit (clone-and-repoint flow)

**Trigger**: user clicks "Edit" on a schema with referencing Scores.

**Modal/drawer copy**:
> Editing this schema will create a new version. The current Assessments using it will be repointed; previous Scores keep referring to the old version. *Trend continuity is preserved for unchanged field names.*

**Below the explainer, two action paths**:

1. **Additive change** (add field / loosen constraint): show fields, let user add. Inline note: "Adding a field is a clone-and-repoint — old data unaffected."
2. **Type change** (D-8 — UI **requires** picking a new name on type change): the field-type dropdown auto-disables and a "Rename field" affordance appears. Old name + value remain in old schema; new schema has new-name with new type.
3. **Rename**: explicit checkbox "Treat as a rename" with warning copy: *"Renaming `accuracy` → `factuality` breaks trend continuity for this field. Old Scores will keep the old name. You own the consequence."*

After save:
- New `AssessmentSchema` row created.
- Pop a toast listing which Assessments were repointed.
- Surface the successor chain on the detail page.

### S5 — AutomatedScorer editor (modal/drawer)

**Replaces**: `templates/evaluations/evaluator_form.html`.

**Two kinds**: `LLM_JUDGE` and `PYTHON`. Form shape differs.

**Common fields**:
- Display name (required, unique within Assessment).
- `output_fields` — multi-select of fields from the Assessment's `AssessmentSchema`. Defaults to all fields. Per D-10: each scorer may cover a subset. Inline validation: at least one field selected; can't reference fields not in the schema.
- "Show in concordance" — derived, not toggled (any scorer producing ≥1 field in common with another scorer auto-participates).

#### S5a — LLM judge

- Model picker (from team's `LlmProvider` rows).
- Prompt — multiline editor with template-variable hint chips (`{{ input }}`, `{{ output }}`, `{{ history }}`, `{{ context }}`). The chip list should mirror what `schema_to_pydantic_model` expects to consume.
- Temperature / top_p / structured-output toggle (advanced, collapsed).
- **Preview run** — "Test on 10 sample items" button per FR-3.5. Returns inline result table; non-destructive (results not persisted to Score).

#### S5b — Python evaluator

- Code editor (monospace, syntax-highlighted).
- "Inputs available" reference — same template variables as the LLM judge.
- Test runner — same 10-item preview as S5a but executing the code.

### S6 — HumanScorer editor (modal/drawer)

**Replaces**: the per-queue fields on `templates/human_annotations/queue_form.html` (assignees, schema, review count) — but the schema is now picked at the Assessment level, not per-queue.

**Fields**:
- Display name (required).
- `output_fields` — same semantics as automated scorer (D-10). Defaults to all schema fields. Lets one Assessment have, say, an LLM judge scoring `accuracy` + `tone` and a human reviewing only `tone`.
- **Assignees** — M2M picker against team members who have the `ANNOTATION_REVIEWER` permission (see [`apps/teams/backends.py:229`](../../../apps/teams/backends.py)). Inline note: "Reviewers only see queues they're assigned to."
- **`num_reviews_required`** — int 1–10. Inline helper: "How many independent reviews each item needs before it's complete."
- **`irr_sample_rate`** — decimal 0.0–1.0 (default 0.0). Slider or percentage input. Helper: "Fraction of items that get one extra reviewer for inter-rater reliability measurement." Per D-11.
- **Prior-score visibility (D-7)** — two toggles, side-by-side, with copy that explains the difference:
  - `show_prior_automated_scores` — *"Show the LLM judge's score above the form."* Default off. Recommended for **calibration** workflows (Story 2).
  - `show_prior_human_scores` — *"Show other reviewers' scores on the same item."* Default off. Keep off for **inter-rater reliability** (Story 9).
- **Per-item assignment** (FR-4.4 / backlog #6) — toggle "Assign specific items to specific reviewers". When on, surfaces a "Assignments" sub-section in the Reviews tab (defined in brief 03).

**Recommended-preset chips** (helpful but skippable): "Single review", "Consensus (3 reviewers)", "Calibration vs judge", "IRR 20% sample". Clicking a preset fills the form with the right combination per the [story-mapping table](../unified-assessment.md#how-the-user-stories-map-to-the-unified-design).

## Cross-cutting concerns

### Permissions

- **Create/edit/delete Assessments, schemas, scorers** — team admins (per FR-10.1, FR-10.2).
- **View Assessment list and detail** — anyone on the team (FR-10.5).
- **Edit Reviews** — only the reviewer who submitted, plus team admins (brief 03).
- **Cannot delete** an Assessment that has Scores referencing it — archive-only (existing OCS pattern, `archived_at`).

### Feature flag

The entire surface is gated behind a single team-managed Waffle flag (FR-10.6). Suggested name: `ASSESSMENTS`. The pre-unified routes (`/evaluations/`, `/human_annotations/queues/`) remain available behind their existing flags during transition.

### Empty-team experience

A team with the `ASSESSMENTS` flag on but no Assessments, no schemas, and no scorers should see a single "Create your first Assessment" landing screen with three example presets (Calibrate a judge · Continuously score with LLM judge · Set up a human review queue) that pre-fill the creation flow. Avoid an empty table; avoid showing the schemas-list and scorers list separately when there's nothing to show.

### Archive vs delete

- Schemas: no archive (catalogue is append-only per D-8).
- Assessments: archive (set `archived_at`); hidden from default list, retrievable via "Show archived" filter.
- Scorers, RoutingRules: belong to an Assessment — destroyed with the Assessment, edited otherwise.

### Validation invariants the UI must enforce (or surface clearly)

- Schema picked at step 3 must include every field referenced by any scorer's `output_fields` at step 4. If user removes a field from the schema that a scorer references, block.
- A scorer's `output_fields` cannot be empty.
- `num_reviews_required` ∈ [1, 10]; `irr_sample_rate` ∈ [0.0, 1.0].
- Field name uniqueness within a schema; field names must not collide with system-reserved names (`user_thumb`, etc. — per D-4).
- On schema type-change: forbid edit-in-place, force rename (per D-8 and S4c).

## Open design questions

1. **Wizard vs single-page form for creation (S2).** Stepped form is the draft recommendation because the source/schema/scorer choices interact (a poorly-chosen mode forces a rework later). But it's heavier than a single-page form. Decision pending — try wizard, fall back to single-page if usability testing surfaces friction.
2. **Schema catalogue as top-level nav vs nested under Assessments.** Drafted as top-level (left rail) to make reuse visible. Alternative: under Assessments only, with a "browse all" link. Open.
3. **Scorer naming convention surfaced to users.** Today's `Evaluator` rows have free-text names; the unified design doesn't change that. Should "name" of an automated scorer default to something like `LLM judge — accuracy, tone` to make scorer cards self-describing? Probably yes, but spec leaves to designer.
4. **Per-Assessment authoritative-Review admin override (D-16).** Setting a Review authoritative manually requires a permission — where does this UI affordance live? Probably brief 03 (Reviews tab on the Assessment detail). Confirm during review of this brief.
5. **Inline schema editor vs full-page (S2 step 3).** A user creating their first Assessment shouldn't have to leave the wizard to make a schema. Mini-form is drafted; if the full editor's complexity exceeds modal capacity (e.g. for `Choice` with many options), promote to full page with auto-save and return.

## Cross-references

| Topic | Where |
|---|---|
| Why two prior-score visibility knobs, not one | [D-7](../unified-assessment.md#d-7-two-prior-score-visibility-knobs-on-humanscorer-not-one) |
| Why shared schema with per-scorer `output_fields` | [D-10](../unified-assessment.md#d-10-shared-schema-with-per-scorer-field-subsets-not-schema-per-scorer) |
| Why schema editing is clone-and-repoint | [D-8](../unified-assessment.md#d-8-assessmentschema-is-a-real-catalogue-not-embedded-json) |
| Why IRR is a separate sampling rate | [D-11](../unified-assessment.md#d-11-irr-sampling-is-a-separate-field-sampled-at-queue-entry) |
| Why bot_version is a run parameter, not config | [D-3](../unified-assessment.md#d-3-bot_version-lives-on-assessmentrun-not-on-the-assessment) |
| Existing concordance template as DaisyUI reference | [`templates/assessments/concordance.html`](../../../templates/assessments/concordance.html) |
| Schema field type definitions | [`apps/evaluations/field_definitions.py`](../../../apps/evaluations/field_definitions.py) |
| Reviewer permission group | [`apps/teams/backends.py:229`](../../../apps/teams/backends.py) |
