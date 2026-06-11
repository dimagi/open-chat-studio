# 03 — Review workflow (HumanScorer)

> The screens reviewers see when they review an item. Two audiences: **reviewers** (working through an assigned queue, submitting Reviews) and **team leads / admins** (managing the queue, assigning items, overriding with authoritative Reviews). One Assessment with a `HumanScorer` powers all of this.
>
> **Anchored to** [`../unified-assessment.md`](../unified-assessment.md): D-7 (two prior-score visibility knobs), D-10 (per-scorer `output_fields`), D-11 (IRR sampling at queue entry), D-14 (`AppliedRoutingRule` provenance for escalated items), D-16 (authoritative Reviews resolve disagreement).
>
> **Out of scope** — covered by other briefs: HumanScorer configuration (brief 01); the rule that triggered an escalation (brief 02); aggregating reviews into concordance/trends (brief 04).

## User stories addressed

- Story 2 — *Manual calibration of LLM judges* (reviewer sees prior automated score per D-7).
- Story 5 — *Human review queue, judge-flagged*.
- Story 6 — *Human review queue, user-feedback-flagged*.
- Story 9 — *Inter-rater reliability* (multi-review consensus + IRR sampling per D-11).
- Story 10 — *Second-pass review* (HUMAN_FLAG → escalation showing flagging reviewer's score).
- Adjudication side of D-16 — *Resolving disagreement* with authoritative Reviews.

## Information architecture

```
Assessment detail
├── Overview / Source / Scorers / Routing  (briefs 01, 02)
├── Reviews          ← THIS BRIEF (admin / team-lead view; only when ≥1 HumanScorer)
└── Runs / Trends / Concordance            (brief 04)

Top-level Reviewer surface
└── /my-reviews/     ← THIS BRIEF (reviewer's personal queue across all assigned HumanScorers)
    └── /<review_item_id>/  ← THIS BRIEF (the actual review screen)
```

**Why two entry points to the same review screen**:
- **Reviewer** lands at `/my-reviews/`, sees items across every Assessment they're assigned to, works through them. They don't think in terms of Assessments — they think *"I have 12 items to review."*
- **Admin** lands at the Assessment detail's Reviews tab, sees the queue *for one Assessment*, manages assignments and flagged items, occasionally overrides with an authoritative Review.

Both paths converge on the same per-item review screen (S10).

## Screens

### S9 — Reviewer's queue (`/my-reviews/`)

**Replaces**: today's `templates/human_annotations/queue_detail.html` rendered for a reviewer. Conceptually a personal inbox across all assigned HumanScorers.

**Purpose**: give a reviewer a single place to come back to and chip away at outstanding work without thinking about which Assessment owns what.

**Primary user**: Reviewer (member of `ANNOTATION_REVIEWER_GROUP`, see [`apps/teams/backends.py:229`](../../../apps/teams/backends.py)).

**Top section — summary stats**:
- Open items count (across all assigned HumanScorers).
- Today's submissions count.
- Flagged items count (mine + items routed back to me for second-pass).
- Items where `is_irr_sample = True` and the IRR slot is open (small chip per D-11).

**Main list**:
- Grouped by Assessment, collapsed-by-default cards. Each card shows: Assessment name, open count, "Review next" button.
- Items list per card (when expanded): item identifier (target id / external session id), priority hint, status (pending / in-progress / awaiting-flag-review), age.
- **IRR badge** on items where `is_irr_sample = True` (per D-11) — visually distinct but not loud; reviewers benefit from knowing, but should review the same way regardless.
- **Escalation badge** on items routed to this reviewer via `HUMAN_FLAG` or `HUMAN_DISAGREEMENT` (per `AppliedRoutingRule.outcome` per D-14) — clarifies "why am I seeing this twice".

**Primary actions**:
- "Review next" (per Assessment) — opens the next-up `ReviewItem` (S10).
- Click a specific item in the list → opens that item (S10).

**Empty state**: *"All caught up. Nothing assigned to you right now."* Plus a small "Where do items come from?" link to a doc explaining sources, routing, and assignment.

### S10 — Per-item review screen

**Replaces**: `templates/human_annotations/annotate.html`. Same core shape — show the item, render the form, submit/flag — but with the D-7 visibility knobs, IRR awareness, escalation context, and authoritative flag affordances.

**Primary user**: Reviewer (assignee of the HumanScorer).

**Layout — three regions**:

```
┌─────────────────────────────────────────────────────────────────┐
│  Header: Assessment name · HumanScorer name · "Reviewing item   │
│  N of M" · escalation badge (if any)                            │
├─────────────────────────────────┬───────────────────────────────┤
│                                 │                               │
│  LEFT: the item under review    │  RIGHT: the review form       │
│  (session transcript, trace,    │  (schema-driven inputs,       │
│   dataset message — pivots on   │   prior-score panel,          │
│   target type per D-13)         │   flag, submit, draft)        │
│                                 │                               │
└─────────────────────────────────┴───────────────────────────────┘
```

#### S10a — Item display (left pane)

Pivots on Score target type (per D-13):

- **`ExperimentSession` target** (session-granularity): full conversation transcript, with role chips, timestamps, and per-message metadata expandable. Reuse the existing chatbot session view shape (see [`templates/chatbots/manage/`](../../../templates/chatbots/manage/)).
- **`Trace` target** (message-granularity): the specific LLM call — prompt, response, error, duration, participant. Use existing trace view shape (`templates/trace/`).
- **`EvaluationMessage` target** (dataset-granularity, offline): input / output / context / history fields as the dataset edit view shows them today.

**Common controls**: copy-id, jump-to-source (open the session/trace in its native page).

#### S10b — Form (right pane)

**Header strip**:
- Item status (pending / in-progress / completed-by-others-but-i-still-need-to-review).
- Reviews-needed badge: *"This item needs N reviews. M done."* Includes IRR `+1` if applicable per D-11.
- **Escalation context block** (only if this item arrived via a `HUMAN_FLAG` or `HUMAN_DISAGREEMENT` routing rule, traced via `AppliedRoutingRule.triggered_by`):
  - For `HUMAN_FLAG`: *"@alice flagged this item: '<flag reason>'. Her review was: …"* — flagging reviewer's score is **always shown on second-pass**, hardcoded per D-7's note on Story 10. Do *not* gate this on `show_prior_human_scores`.
  - For `HUMAN_DISAGREEMENT`: *"Reviewers disagreed on `<field>`: @alice = 3, @bob = 1. You're the adjudicator — your submission will be marked authoritative."* (When the triggering rule has `mark_authoritative=True`.)

**Prior-score panel** (rendered conditionally per D-7):
- **Prior automated scores** — visible iff `HumanScorer.show_prior_automated_scores = True`. Render each automated scorer's value(s) for fields in the schema, with the scorer's name and timestamp. Subtle styling; don't dominate the form.
- **Prior human scores** — visible iff `HumanScorer.show_prior_human_scores = True`. Render each other-reviewer's Review, with author name, timestamp, value(s).
- **Escalation-provenance score** — visible regardless when escalated (Story 10's hardcoded behaviour). Override the `show_prior_human_scores=False` default in this one case.

**Schema-driven form** (per FR-1, FR-4.1):
- Only the fields in `HumanScorer.output_fields` (D-10) — reviewer doesn't see fields they're not asked to score.
- Per-field input control by type:
  - `String` — multiline.
  - `Int` / `Float` — number with optional slider for bounded ranges.
  - `Choice` — radio if ≤6 options, select if more.
  - `Boolean` — segmented toggle (Yes / No).
- Per-field optional comment ("Why?" subfield).
- Field-level required-marker per the schema.

**Action bar (bottom)**:
- **Submit** — primary CTA. Validates required fields; creates one `Score` row per field per the design doc.
- **Save draft** — secondary (FR-4.9 Could). Persists `Review.status = draft`. Reviewer can come back to it.
- **Flag** — opens a small reason-input. Sets the item to `flagged` status; appends to flag history (`ReviewItem`).
- **Unflag** — visible only for items currently flagged by this reviewer (FR-4.7).
- **Skip** — *"I've already reviewed something equivalent / not my expertise."* Doesn't write a Review. Hides from this reviewer's queue. (FR-4.8 semantics.)

**Mid-form helpers / footguns**:
- If `Choice` field has an "allow-other" option, render the free-text "other" only when "Other" is selected (avoid noise).
- Auto-save drafts every 30s (silent), so a closed tab doesn't lose work. Show a small "Draft saved 12s ago" footer.

**States**:
- *In-flight by another reviewer*: if `num_reviews_required > 1` and someone else has the form open, show a quiet *"@bob is reviewing too"* indicator (best-effort, not pessimistic locking).
- *Skipping all items*: if every item left is the reviewer's own (FR-4.8 — skip items already reviewed by current user), surface the all-caught-up message (S9 empty state).
- *Item was unflagged and reset*: if a reviewer comes back to find an item they previously flagged is now re-opened, banner *"This item was unflagged by an admin and needs a fresh review."*

#### S10c — Adjudication mode

A specialisation of S10 triggered when `AppliedRoutingRule.triggered_by` is a disagreement (D-16). Same screen, but:

- Header shows *"Adjudication"* badge.
- Prior-score panel shows **all** participating reviews side-by-side (override the visibility flags for this case).
- Submit button label changes to **"Submit as authoritative"** when the routing rule had `mark_authoritative=True` — the submitted Review's `is_authoritative` is set on save.
- A subtle explainer below the submit button: *"This review overrides the per-source consensus on the fields you submit."*

### S11 — Reviews tab (Assessment detail; admin view)

**Replaces**: today's `templates/human_annotations/queue_detail.html` rendered for admins. The admin view of the work-in-progress queue.

**Primary user**: Team Lead, team admin.

**Top section — queue health stats**:
- Open / completed / flagged counts.
- Average reviews-per-item vs `num_reviews_required` target.
- IRR sample-rate actual vs configured target (per D-11).
- Disagreement-rate per field (count of items where reviewers split, optionally with a stdev pill for numeric).

**Main table — `ReviewItem`s**:

**Columns**: item identifier · status pill · review-count badge (e.g. *2/3*) · IRR badge · flag indicator · most-recent reviewer · age · actions.

**Per-row actions**:
- View item (opens S10 read-only for admin if completed, editable if assigned to that admin).
- **Assign reviewer** — only enabled when `HumanScorer.per_item_assignment = True` (FR-4.4 / backlog #6). Picker from the HumanScorer's assignees. Per-item assignment is a property of `HumanScorer`; brief 01 owns the toggle.
- **Manual override → submit authoritative Review** — D-16's manual path. Opens S10 in adjudication mode; submitted Review's `is_authoritative` is set on save.
- **Re-open** — for items closed prematurely (rare; team-lead only).

**Filters**: status, flagged, IRR sample, has-escalation-history, assigned to me, schema-field-with-disagreement, date range.

**Bulk actions** (checkbox column): bulk re-assign · bulk close (only when their `num_reviews_required` is met).

**Empty state**: *"No items in this queue yet. Items arrive from this Assessment's Source (continuous) or its Routing rules (escalations)."* with a link to the Source and Routing tabs.

### S12 — Manage assignees (sub-modal off Scorers tab)

**Replaces**: `templates/human_annotations/manage_assignees.html`. Same intent, simpler shape because the HumanScorer is one row inside an Assessment now (not its own page).

**Reachable from**: Scorers tab (brief 01) — "Manage assignees" affordance on a HumanScorer row.

**Purpose**: M2M-pick which team users can review this HumanScorer's queue.

**Sections**:
- **Eligible users** — list of team members with the `ANNOTATION_REVIEWER` permission. Inline note linking to the permission docs for users who don't have it.
- **Currently assigned** — checkboxes; bulk-add and bulk-remove.

**Edge cases**:
- Removing an assignee with open in-progress reviews — confirm prompt; offer *"Reassign their open items to: …"* picker. Don't silently orphan.
- Adding a non-reviewer (someone without the permission) — block with explanatory copy; offer a link to grant the permission if the current user is a team admin.

## Cross-cutting concerns

### Permissions matrix

| Action | Required permission |
|---|---|
| See `/my-reviews/` and review assigned items | `ANNOTATION_REVIEWER` group membership |
| Submit a Review (`is_authoritative=False`) | assigned to the HumanScorer |
| Submit a Review (`is_authoritative=True`) via routing rule | assigned to the HumanScorer **and** the routing rule had `mark_authoritative=True` |
| Submit a Review (`is_authoritative=True`) manually | team admin / team lead (D-16: *"explicit toggle in the UI on any Review"*) |
| Reassign a `ReviewItem` to a different reviewer | team admin |
| Unflag a `ReviewItem` (admin path) | team admin |
| View Reviews tab (admin) | team admin |
| Configure a HumanScorer | team admin (brief 01) |

The reviewer-flag (FR-4.7) and adjudication path are distinct:
- Reviewer flags item → `ReviewItem.status = flagged` → if a `HUMAN_FLAG` routing rule exists, escalates to a different reviewer.
- Disagreement detected (D-16) → if a `HUMAN_DISAGREEMENT` routing rule exists with `mark_authoritative=True`, escalates to an adjudicator whose Review becomes authoritative.

### Form generation from schema

The form on the right pane (S10b) is generated from `AssessmentSchema.fields ∩ HumanScorer.output_fields`. The renderer can reuse the existing dynamic-form builder ([`apps/human_annotations/forms.py:build_annotation_form`](../../../apps/human_annotations/forms.py)) with adaptations for the new model shapes.

### IRR sampling — visibility decisions

Per D-11, the IRR flag is set at `ReviewItem` creation (`is_irr_sample`), not at review time. UI implications:

- Show the IRR badge in S9 (reviewer queue) and S11 (admin table) — but **don't** show the flag inside the review form itself. Reviewers should review the item the same way regardless; surfacing it on the form invites bias.
- Open question: should reviewers know an item is IRR-sampled at all? Drafted as yes-in-queue, no-in-form. Worth confirming.

### Authoritative override — discoverability

D-16 says authoritative Reviews can be set procedurally (routing rule action) or manually (admin override). The manual path needs to be discoverable but not casual — listed as a per-row action in S11 with a confirm dialog, not a glaring button.

### Reviewer feedback loop

When a reviewer skips items (FR-4.8), flags items (FR-4.6), or saves drafts (FR-4.9), the action should produce a quiet toast confirmation. Avoid full-page reloads — these actions are high-frequency.

## Open design questions

1. **Reviewer's `/my-reviews/` ordering.** Drafted as grouped-by-Assessment. Alternative: chronological (oldest-first) across Assessments. Decision pending — depends on which signal the reviewer prioritises ("clear oldest backlog" vs "focus on one Assessment at a time").
2. **IRR badge in the review form (S10b).** Currently hidden to avoid bias; alternative is to surface it for transparency. Worth testing.
3. **Real-time "in-flight by another reviewer" indicator (S10b).** Useful but requires a presence mechanism. Could be deferred to v2 if the infrastructure cost is high; designers should know the v1 baseline is no presence.
4. **Read-only review for admins.** When an admin opens a completed item from S11, should they see exactly what the reviewer saw (including prior-score panels for that reviewer) or a god-view (all reviews, all scores)? Drafted as god-view; confirm.
5. **Field-level partial authority** (D-16 final paragraph). The design doc notes that partial-field authoritative override is expressed by submitting a Review with only the disputed fields. UI: does S10c surface a "submit only `<disputed_field>`" affordance, or do we expect the adjudicator to leave other fields blank? Defer to a follow-up usability decision.
6. **Skip semantics.** FR-4.8 says "skip items already reviewed by the current user." Today the system enforces this automatically. The Skip button in S10b is for the explicit *"not my expertise"* case — should the action be just *"return to queue"* (item stays open for someone else to grab) or *"never show me this again"* (item gets a per-user skip record)? Recommend the former for v1.

## Cross-references

| Topic | Where |
|---|---|
| Why two prior-score visibility knobs | [D-7](../unified-assessment.md#d-7-two-prior-score-visibility-knobs-on-humanscorer-not-one) |
| Per-scorer `output_fields` (form generation) | [D-10](../unified-assessment.md#d-10-shared-schema-with-per-scorer-field-subsets-not-schema-per-scorer) |
| IRR sampling at queue entry | [D-11](../unified-assessment.md#d-11-irr-sampling-is-a-separate-field-sampled-at-queue-entry) |
| Authoritative Reviews — when + how | [D-16](../unified-assessment.md#d-16-reviewer-disagreement-is-resolved-by-authoritative-reviews-not-by-statistical-fiat) |
| `AppliedRoutingRule` provenance for escalations | [D-14](../unified-assessment.md#d-14-audit-row-generalises-across-all-routing-rule-action-types) |
| Score targets (Trace / Session / EvaluationMessage) | [D-13](../unified-assessment.md#d-13-score-targets-are-measurement-units-trace-experimentsession-evaluationmessage-not-display-surfaces) |
| Existing dynamic-form builder | [`apps/human_annotations/forms.py:build_annotation_form`](../../../apps/human_annotations/forms.py) |
| Existing annotate template (the shape S10 replaces) | [`templates/human_annotations/annotate.html`](../../../templates/human_annotations/annotate.html) |
| Reviewer permission group | [`apps/teams/backends.py:229`](../../../apps/teams/backends.py) |
