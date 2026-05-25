# 04 — Analytics: Runs, Trends, and Concordance v1

> The three analytical tabs on an Assessment. **Runs** (batch only) for "did this run pass?" and "did v4 beat v3?". **Trends** (continuous only) for "is production drifting?". **Concordance v1** for "do humans and judges agree?". Three distinct views on top of the same `Score` table (D-6).
>
> **Anchored to** [`../unified-assessment.md`](../unified-assessment.md): D-3 (`bot_version` is a per-run parameter), D-5 (continuous has no Runs), D-6 (Runs and Trends are separate views), D-13 (Score targets), D-16 (authoritative Reviews override consensus).
>
> **Out of scope** — covered by other briefs: Source / Routing / Scorers config (briefs 01, 02); the act of reviewing (brief 03). The Concordance v0 prototype at [`templates/assessments/concordance.html`](../../../templates/assessments/concordance.html) is the starting point this brief evolves *from*.

## User stories addressed

- Story 3 — *Regression checks across versions* (Runs tab; per-`AssessmentRun` `bot_version` per D-3).
- Story 7 — *Concordance* (Concordance tab).
- Story 8 — *Trend monitoring* (Trends tab).
- Story 9 — *Inter-rater reliability* (per-author breakdown inside Concordance — the design doc's "group by `Score.author`" path).

## Information architecture

```
Assessment detail
├── Overview / Source / Scorers / Routing / Reviews   (briefs 01–03)
├── Runs          ← THIS BRIEF (batch only)
│   ├── /<run_id>/                  (run detail)
│   └── /compare/<run_a>/<run_b>/   (side-by-side comparison)
├── Trends        ← THIS BRIEF (continuous only)
└── Concordance   ← THIS BRIEF (any Assessment with ≥2 scorer types or ≥2 reviewers)
```

**Tabs that don't apply are hidden, not greyed.** A batch Assessment doesn't show Trends; a continuous Assessment doesn't show Runs (D-5). An Assessment with only one scorer doesn't show Concordance.

## Screens

### S13 — Runs tab (batch only)

**Replaces**: `templates/evaluations/evaluation_runs_home.html`. Same intent — list of runs, drill into one. New: per-run `bot_version` selection (D-3) is a runtime parameter.

#### S13a — Runs list

**Top section — quick stats** (across all runs of this Assessment):
- Latest run status pill.
- Latest aggregate per scorer (chips).
- "Compare two runs" button — opens the comparison picker (S13c).

**Main table — `AssessmentRun`s**:

**Columns**: started · finished · duration · status · **bot version chip** (per-run; D-3) · scorer breakdown (small icons + per-scorer aggregate) · total Scores · row actions.

**Status pill values** (from `AssessmentRun.status`): pending / processing / completed / failed. Match the existing pattern in [`templates/evaluations/evaluation_run_status_column.html`](../../../templates/evaluations/evaluation_run_status_column.html).

**Per-row actions**: view detail (S13b) · re-run with same config · download CSV · download JSONL (FR-5.6).

**"Start a run" CTA** (top-right):
- Opens a small modal — pick `bot_version` (per D-3: specific / latest-working / latest-published — three choices, defaults to latest-working), confirm dataset size, "Start".
- Background note: continuous Assessments don't have this CTA — the Runs tab itself is hidden for them.

**Empty state**: *"No runs yet. Start a run to evaluate this Assessment against its dataset."* Single CTA.

#### S13b — Run detail

**Header**: run name (auto = *"Run N — `<bot_version_chip>` — `<started_at>`"*), status, started/finished/duration, total Scores.

**Sections**:

1. **Per-scorer summary** — one card per scorer:
   - Scorer name + kind icon.
   - **Aggregate panel** — per field in `output_fields`: aggregate value (mean for numeric, mode + distribution for categorical, true% for boolean), N, stdev. Sourced from `AssessmentRunAggregate` (eager batch cache per D-5).
   - "Drill into Scores" link — opens S13d (per-scorer score table).
   - Error count chip (errors are on `AutomatedResult`, not on Score — surface them here as workflow signal).
2. **Routing-rule firings card** — count of `AppliedRoutingRule` rows produced by this run (per D-14). Click → S8c filtered to this run.
3. **CSV / JSONL download** (FR-5.6) — per-run, with all FR-5.5 columns (global session id included).
4. **CSV upload to correct/override** (FR-5.7) — batch-only feature. Surface as a small "Override results" affordance with explainer copy about re-aggregation.

#### S13c — Run comparison (Story 3)

Reachable from S13a's "Compare two runs" button.

**Picker step**: choose Run A (default: latest), choose Run B (default: previous). Both must be on the same Assessment.

**Comparison page**:

- **Header strip**: Run A `<chip>` ↔ Run B `<chip>`, total Scores per side, bot_version per side.
- **Per-field side-by-side table**:
  - Rows: each field in the Assessment's schema.
  - Columns: aggregate A, aggregate B, delta (with arrow + colour for direction-of-improvement, agnostic to "better is up/down").
  - For numeric: mean ± stdev (A), mean ± stdev (B), delta.
  - For categorical: mode % (A), mode % (B), distribution chips collapsed by default.
- **Per-target diff drilldown** — *"Show me items where A and B disagree most."* Sortable table of targets with both A and B values, sorted by abs(delta). Useful for surfacing regressions on specific dataset items.
- **Export**: CSV per the per-field side-by-side table.

#### S13d — Per-scorer score drilldown

A drilldown from S13b. Sortable, filterable table of every `Score` row produced by one scorer in one run.

**Columns**: target (deep link) · per-field values (one column per `output_field`) · `AutomatedResult` status (success / error) · created_at · view (opens raw `AutomatedResult` JSON in a modal).

Carries forward the existing `templates/evaluations/evaluation_results_table.html` shape; the new piece is that the values are now from Score rows, not from a dict on `EvaluationResult.output`.

### S14 — Trends tab (continuous only)

**Purpose**: answer Story 8 ("is production drifting?") with a rolling time-window view over the Score table.

**Primary user**: Bot Owner, Team Lead.

**Top section — time-window + filter chrome**:
- **Time window** — common ranges (last 24h / 7d / 30d / 90d / custom). Default: last 7 days.
- **Granularity** — hour / day / week (auto-selected based on window).
- **Source filter** (FR-6.7) — multi-select: any combination of `LLM_JUDGE`, `PROGRAMMATIC`, `HUMAN_REVIEW`, `USER_FEEDBACK`, `SYSTEM`. Default: all.
- **Field filter** — multi-select of fields in the Assessment's `AssessmentSchema`.
- **Authoritative-only toggle** (D-16) — for the human-side: optionally include only Scores from `Review.is_authoritative=True`. Helpful when adjudicated ground truth is the comparator.

**Main section — one chart per selected field**:

- **Numeric fields** — line chart with mean ± std-band per bucket, one line per source. Optional median overlay. Y-axis = field value range.
- **Boolean / Choice fields** — stacked area (proportion per category over time). For boolean, simplifies to "% true".
- **Score volume** — small line chart of Scores-per-bucket (sanity-check; sudden drops imply dispatch problems — link to `AppliedSourceFilter` audit in S7c).

**Side panel — current snapshot card**:
- Latest aggregate per field per source.
- "Compared to 7 days ago" delta arrow.
- Link to the underlying score query (for export / power users).

**Health card** (re-used from S7b):
- Latest Score timestamp, 24h Score count, 24h `AppliedSourceFilter` failure count.

**Empty state**: *"No Scores in this window. Continuous scoring depends on lifecycle events — see the [Source](../source-tab) tab for dispatch health."*

**No materialised continuous-trend aggregates** (per the "Out of scope" callout in the design doc): queries compute on demand from `Score` rows. If query cost becomes a problem, the optimisation is precomputed daily/weekly buckets — additive, no UI change needed.

### S15 — Concordance v1

**Replaces**: `templates/assessments/concordance.html` (the v0 live for testing). The v0 already nails the basic shape — eval/queue picker, single shared categorical field, agreement %, matched/eval-only/human-only stats, deep links. v1 evolves it from "first useful thing" to "first useful thing across the full design surface".

**Primary user**: Bot Builder, Team Lead.

#### What v0 already does well (carry forward unchanged)

- Picker UX: pick A, pick B, pick field, pick `show` filter (matched / eval-only / human-only / all).
- Headline agreement %.
- Stats strip with matched/eval-only/human-only counts.
- Per-row deep links to eval result and annotation item.
- Empty-state messaging per `show` value.

#### What v1 adds

1. **Picker generalisation**:
   - Instead of "Evaluation × Annotation queue", pick **Assessment** + **Source A** + **Source B**.
   - Sources are *any* scorer-source combination producing Scores on overlapping targets: e.g. *"LLM judge: accuracy"* vs *"Human consensus: accuracy"*, or *"LLM judge A"* vs *"LLM judge B"*, or *"Reviewer alice"* vs *"Reviewer bob"* (the inter-rater path per Story 9).
   - In effect: source dropdown enumerates `(scorer, source_enum)` combinations producing Scores in the chosen Assessment, plus optional `Score.author` for the human-vs-human path.
2. **Multi-field at once**:
   - Drop the single-field selector; show one panel per shared field. Within a panel, the agreement headline + per-row table are scoped to that field.
   - Field-tabs at the top for navigation when there are many fields.
3. **Field-type-aware agreement statistics** (FR-7.3–7.5):
   - **Categorical** — % agreement (already in v0) **plus** Cohen's Kappa with N and CI. Confusion matrix below the headline (collapsible).
   - **Numeric** — Pearson correlation, mean absolute error, bias (mean of A - B), N. Scatter plot below the headline (collapsible). Bland-Altman is a v2 nice-to-have, not required.
   - **Boolean** — both (categorical with 2 levels + numeric-style agreement).
4. **Disagreement triage** (FR-7.6):
   - "Sort by disagreement (largest first)" — for numeric, by abs(delta); for categorical, by "is disagreement" + freshness.
   - One-click "Send disagreements to adjudication" — opens a confirm that creates `ReviewItem`s under an adjudicator HumanScorer (or surfaces a "no adjudicator HumanScorer exists, configure one in Routing" affordance, linking to brief 02).
5. **Pre-aggregation transparency** (per the design doc's "Concordance is not a raw join on Score rows" callout):
   - One-liner per source explaining how the side was computed: *"Source A: LLM judge — latest Score per target."* *"Source B: Human consensus — mean across N reviewers per target."* *"Source B: Reviewer alice — raw Scores (IRR path)."*
   - If `is_authoritative=True` Reviews exist on the field, the consensus uses those preferentially (D-16 / FR-6.8). Surface this as a chip: *"Includes 3 authoritative reviews on `<field>`"*.
6. **Filter affordances** (FR-7.8): date range, experiment filter (when target = `ExperimentSession`), tag filter.
7. **Export** (FR-7.7): CSV per field + an "all fields" combined export.

#### Layout sketch (v1)

```
Header: Assessment ▾   ·   Source A ▾   ·   Source B ▾   ·   Date range ▾   ·   [Filter chips]

Field tabs: [accuracy] [tone] [helpfulness] [safety]

┌── accuracy ─────────────────────────────────────────────────────────────┐
│ Stats strip:  Agreement 84%  ·  κ = 0.71 (N=312)  ·  matched 312        │
│               eval-only 14  ·  human-only 9                             │
│                                                                         │
│ Confusion matrix (collapsible)         Disagreement triage (top 20)     │
│   [confusion table]                    [sortable list of mismatches]    │
│                                                                         │
│ Pre-aggregation note: Source A = LLM judge latest per target;           │
│                       Source B = Human mean across 3 reviewers          │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Cross-Assessment concordance (explicitly out of scope)

Per FR-7 and the design doc: concordance is **within-Assessment only** in v1. The picker only lists sources from the chosen Assessment. If a user expects cross-Assessment concordance, surface a small disclaimer near the picker: *"Comparing across Assessments isn't supported — both sides must score the same items. See [docs](...)."*

#### Inter-rater reliability path (Story 9)

When Source A or B = a specific `Score.author` (a single reviewer) and the other side = consensus or another reviewer:

- Pre-aggregation note clarifies: *"Source A = Reviewer alice (raw Scores, no aggregation)."*
- Stats interpretation: this is IRR, not eval-vs-human. UI copy adjusts: *"Inter-rater reliability between alice and bob"*.
- For the "everyone vs everyone" IRR view: a small *"Show pairwise IRR"* mode that renders an N × N kappa matrix for the HumanScorer's assignees. v1 nice-to-have; defer if scope-tight.

#### Migration from v0

- v0 URL `/assessments/concordance/?eval=<id>&queue=<id>&field=<name>&show=<...>` should redirect to the v1 equivalent under the Assessment detail tab.
- v0 only handled categorical fields; v1's numeric path is net-new.
- v0 always took the **latest** Score per target (`_latest_score_per_target`) — v1 should *default to* consensus when `num_reviews_required > 1` and surface the pre-aggregation note so users see why their numbers changed.

## Cross-cutting concerns

### Where the data comes from

Both Trends and Concordance read directly from the `Score` table. Runs read from `Score` + the eager `AssessmentRunAggregate` cache. Per D-6: no unified "trend abstraction" — three views over one table, each tuned to its question.

### Source filtering interplay (D-16 + FR-6.8)

Aggregation everywhere respects authoritative overrides per D-16/FR-6.8:
- For a `(target, name)` with any authoritative Score, that Score *is* the human-side consensus value for that field.
- Otherwise compute mean/mode normally across the eligible Scores.

UI exposure: a small "Authoritative-only" toggle on Trends and Concordance (where applicable). On Runs (batch), authoritative is rare and not surfaced by default.

### What "consensus" means precisely

The design doc commits to this:
- **Per-source consensus** = mean (numeric) / mode (categorical) of Scores grouped by source within the source-pre-aggregation step. With authoritative override per D-16.
- For IRR Story 9: skip pre-aggregation on the human side; group by `Score.author` instead.

The UI should never silently make a different aggregation choice; if a user changes a filter that alters how a side is computed, the pre-aggregation note (S15 item 5) updates live.

### Performance

- **Runs** views are bounded by Run scope — fast, hits the eager aggregate cache.
- **Trends** is the more expensive path (window scans). Add indexes per the design doc's hint (`(target_content_type, target_object_id, name, source)`, `(created_at)` — both already present per `apps/assessments/models.py`).
- **Concordance** queries are bounded by `(assessment, name)`. Pre-aggregation is the expensive part; cache per (Assessment, source-A, source-B, field, window) is justifiable if the queries get slow — additive optimisation.

### Permissions

- All three tabs are read-by-default for any team member with the `ASSESSMENTS` flag (FR-10.5).
- Starting a run, downloading exports, and "send disagreements to adjudication" require team-admin or appropriate elevated permission.

### Feature-flag transition

While the unified surface rolls out:
- The v0 concordance page (`flag_assessments_concordance`) keeps working until v1 is ready in this tab.
- When `ASSESSMENTS` is fully enabled for a team, the v0 page becomes a redirect to the v1 tab. The two flags coexist briefly.

## Open design questions

1. **Concordance picker UX when source enumeration is large.** Drafted as flat dropdowns; if an Assessment has 5+ scorers, this gets noisy. Worth grouping by source kind (LLM-judge / Programmatic / Human / User-feedback).
2. **Inter-rater pairwise matrix in v1.** Drafted as nice-to-have. Decide: ship in v1 or defer to v2? Depends on Story 9's real urgency.
3. **Cross-Assessment concordance disclaimer placement.** Inline near the picker as drafted, or a separate "Why can't I compare across Assessments?" docs page? Smaller is better.
4. **Trends y-axis bounds.** Drafted as data-driven autoscale; for `Choice` fields with known ranges (0–10 scale, say), should the axis lock to the schema's min/max? Likely yes for legibility.
5. **Run comparison — which side is "before"?** Drafted with A on the left as user-picked; consider a convention where the *older* run is always A so the delta arrow is always *"newer beat older?"*. Tradeoff: less flexible for power users.
6. **CSV upload to override results (S13b section 4 / FR-5.7).** Continuous Assessments explicitly *don't* support this per FR-5.7. Surface clearly so users don't expect it. Where the affordance lives on the Runs detail page is fine; just make sure it disappears for continuous.

## Cross-references

| Topic | Where |
|---|---|
| Runs and Trends are separate views, shared plumbing only | [D-6](../unified-assessment.md#d-6-separate-runs-and-trends-views-shared-score-plumbing-only) |
| `bot_version` on `AssessmentRun`, not config (Story 3) | [D-3](../unified-assessment.md#d-3-bot_version-lives-on-assessmentrun-not-on-the-assessment) |
| Continuous Assessments produce no `AssessmentRun` | [D-5](../unified-assessment.md#d-5-continuous-assessments-do-not-produce-assessmentrun-rows) |
| Concordance pre-aggregation and the inter-rater path | [Score model section](../unified-assessment.md#score-the-value-layer), [story-mapping table](../unified-assessment.md#how-the-user-stories-map-to-the-unified-design) |
| Authoritative Reviews override consensus | [D-16](../unified-assessment.md#d-16-reviewer-disagreement-is-resolved-by-authoritative-reviews-not-by-statistical-fiat), [FR-6.8](../unified-assessment.md#fr-6-aggregation--trends) |
| Existing concordance v0 view (the shape this brief evolves from) | [`templates/assessments/concordance.html`](../../../templates/assessments/concordance.html) |
| Existing eval-runs home (the shape S13 replaces) | [`templates/evaluations/evaluation_runs_home.html`](../../../templates/evaluations/evaluation_runs_home.html) |
| Existing eval-result table | [`templates/evaluations/evaluation_results_table.html`](../../../templates/evaluations/evaluation_results_table.html) |
| Score model + unique constraints | [`apps/assessments/models.py`](../../../apps/assessments/models.py) |
