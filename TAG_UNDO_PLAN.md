# Tag Undo: FULL-run-only undo + run-level `tags_archived` + tooltip attribution

Implementation plan for restricting eval-driven tag undo, per discussion on
[PR #3466](https://github.com/dimagi/open-chat-studio/pull/3466#discussion_r3332783413).

Three parts:
- **Part A** — undo eligibility + active-set tracking via a run-level `tags_archived` flag.
- **Part B** — tag tooltip shows who/what applied an eval-driven tag.
- **Part C** — ADR recording the FULL-only-undo decision.

> **Design note (supersedes the earlier per-row draft).** An earlier version of this
> plan put an `archived` boolean on each `AppliedTag` and resolved undo with a
> per-message walk-back. That work is still uncommitted on this branch (migration
> `0016_appliedtag_archived_and_more.py` is staged, not committed), so it is reworked
> here rather than reversed. The flag moves to the **run**, and the per-message
> machinery is deleted in favour of a run-set computation — both justified by the
> DELTA invariant below.

# The DELTA invariant (load-bearing)

DELTA runs are created in exactly one place — `auto_population.py:33`,
`config.run(run_type=DELTA, scoped_messages=appended)` — where `appended` is the set
of freshly-created `EvaluationMessage`s returned by `_scan_for_new_sessions`. Auto-population:

- only ingests sessions **not already in the dataset** (`auto_population.py:63-67`), and
- dedups by `session_id` (`models.py:296-315`), so a session is added at most once and
  its session-mode snapshot is frozen at ingestion.

Therefore, for any config:

- A **FULL** run evaluates *every* dataset message.
- Each **DELTA** run evaluates a set of brand-new sessions that is **pairwise disjoint**
  from every other DELTA and from every FULL run that preceded it.

The message space is cleanly partitioned across runs. This is what makes both the
run-level flag and the run-set undo correct.

**This invariant is load-bearing and currently enforced only by the single call site.**
The design breaks if a DELTA run is ever created with `scoped_messages` that overlap a
prior run. To keep the contract explicit (Part A step 0), `EvaluationConfig.run` carries a
comment stating DELTA `scoped_messages` must be disjoint from prior runs, and a cheap
guard rejects a DELTA whose scoped messages were already evaluated by an earlier run of
the same config.

# Part A — FULL-only undo + run-level `tags_archived`

## Goal

Restrict eval-driven tag undo to the **latest FULL run**, undoable **once**, and track
which runs' tags are live via a new `tags_archived` boolean on `EvaluationRun`.

## Rationale

DELTA runs only ever *add* tags for disjoint new sessions; they never supersede an
existing session's tags. So there is nothing meaningful to "undo" on a DELTA in
isolation — its tags are reverted (if ever) by undoing the FULL run that re-covered its
sessions. Undoing arbitrary historical FULL runs is also low-value: each FULL run
re-tags the whole dataset, so the only coherent thing to revert is the most recent one.
If tagging is wrong, fix the evaluator and re-run. The flag also addresses traceability
issue #3488 (it records current active state — a boolean records *state*, not an event
log).

## Mechanics — eagerly-maintained active set

- **`EvaluationRun.tags_archived`** — new boolean, default `False`. Invariant: the live
  `CustomTaggedItem` state is mirrored by the `AppliedTag` rows of all non-PREVIEW runs
  with `tags_archived=False`. Given the DELTA invariant, the active set is always
  exactly `{latest FULL run} ∪ {DELTA runs that finished after it}`.

- **On run completion** (hooked at `apps/evaluations/tasks.py:226`, after this run's
  `AppliedTag`s exist):
  - **PREVIEW** → no-op.
  - **DELTA** → no-op. It only adds disjoint new-session tags; nothing is superseded.
  - **FULL** → set `tags_archived=True` on every *other* non-PREVIEW run of this config
    that is currently active, and keep this run active. A single `UPDATE`; no per-message
    scan. `reverse_stale_tags` still runs to reconcile live `CustomTaggedItem` rows.

- **Undo (FULL only)** — restore the immediately-preceding epoch as a run *set*:
  - `P` = latest completed FULL run of the config that finished strictly before this run.
  - `restore_set` = `{P}` ∪ `{DELTA d : P.finished_at < d.finished_at < run.finished_at}`
    (just the intervening DELTAs; if `P` is `None`, `restore_set` is those DELTAs, and the
    undone run's tags simply drop with nothing restored).
  - Reconcile `CustomTaggedItem` at target granularity:
    - `current` = managed tags from the undone run's `AppliedTag`s, resolved to targets.
    - `previous` = managed tags from `restore_set`'s `AppliedTag`s, resolved to targets.
    - per target: delete `current − previous`, add `previous − current`, leave the rest.
  - Flip flags: undone run `tags_archived=True`; every run in `restore_set`
    `tags_archived=False`. Runs older than `P` stay archived.
  - All in one `transaction.atomic()`.

- **Eligibility** — the undo button + view allow undo only on the latest completed
  **FULL** run whose `tags_archived` is `False`. DELTA/PREVIEW are never directly
  undoable. After one undo the run is latest-but-archived → blocked; no walking further
  back.

## Implementation steps

0. **Guard the DELTA invariant** (`apps/evaluations/models.py`, `EvaluationConfig.run`)
   - Add a comment on the `scoped_messages` parameter stating DELTA runs must be passed
     message sets disjoint from prior runs of the same config.
   - When `run_type == DELTA`, cheaply reject scoped messages already evaluated by an
     earlier run of this config (e.g. raise if any of the scoped message ids appear in
     `EvaluationResult.objects.filter(run__config=self).values_list("message_id")`). This
     turns a silent correctness break into a loud failure; the sole live caller
     (auto-population over brand-new sessions) never trips it.
   - Unit test: passing overlapping `scoped_messages` to a DELTA run raises.

1. **Schema** (`apps/evaluations/models.py`)
   - Remove `AppliedTag.archived` and its `Index(fields=["evaluation_result", "archived"])`.
     `AppliedTag` returns to a pure audit row.
   - Add `tags_archived = models.BooleanField(default=False, db_index=True)` to
     `EvaluationRun`, with help text explaining the active-set invariant.
   - Delete the staged migration `0016_appliedtag_archived_and_more.py` and
     `makemigrations evaluations` afresh. Additive `default=False` backfills existing runs
     as active. Backwards compatible.

2. **Archive-on-supersede** (`apps/evaluations/tagging.py`, hooked at `tasks.py:226`)
   - Replace `archive_superseded_tags(run)` (per-message, per-row) with a run-level
     `archive_superseded_runs(run)`:
     - PREVIEW or DELTA → return.
     - FULL → `EvaluationRun.objects.filter(config=run.config, tags_archived=False)
       .exclude(type=PREVIEW).exclude(pk=run.pk).update(tags_archived=True)`.

3. **Undo path** (`undo_run_tags`, `apps/evaluations/tagging.py`)
   - Delete `_message_ids_by_latest_prior_run`, `_previous_applied_by_message`, and the
     per-message branch of `_compute_undo_target_diffs` / `_update_archive_flags_for_undo`.
   - New helpers: `_restore_set_for(run)` (returns `P` + intervening DELTAs) and a
     target-level diff built from `AppliedTag`s of the undone run vs the restore set.
     Reuse `resolve_target` + `_apply_undo_target_diffs` for the `CustomTaggedItem` write.
   - Flip `tags_archived` on the undone run and the restore set inside the same
     transaction as the `CustomTaggedItem` mutation.

4. **Eligibility gate**
   - `can_undo_tags(run) -> bool`: True only when run is COMPLETED, type FULL, the latest
     completed FULL run for its config, and `run.tags_archived is False`.
   - View (`apps/evaluations/views/evaluation_config_views.py:621`): keep the
     `can_undo_tags` check + existing error-redirect on failure.
   - Template (`templates/evaluations/evaluation_result_home.html:46`): gate the undo form
     on a `can_undo_tags` context flag.

5. **Tests** (`apps/evaluations/tests/test_tagging_integration.py` + units)
   - FULL1 → FULL2 → FULL3: only FULL3 undoable; after undoing FULL3, FULL2 live but
     neither FULL2 nor FULL3 undoable; `tags_archived` upholds the active-set invariant.
   - FULL1 → DELTA → DELTA → FULL2: undoing FULL2 restores FULL1 + both DELTAs as a set;
     each DELTA's disjoint new-session tags come back, FULL2's tags drop.
   - FULL1 → DELTA → FULL2 → DELTA → FULL3: undoing FULL3 restores FULL2 + only the DELTA
     between FULL2 and FULL3; FULL1 and the first DELTA stay archived.
   - DELTA run: not undoable (button hidden, view rejects); completing a DELTA archives
     nothing.
   - Manually-added session (no DELTA) tagged only by FULL runs: undo reverts it to the
     prior FULL's tags, or to untagged when no prior FULL covered it.
   - View rejects ineligible undo with the existing error message; PREVIEW unaffected.

## Worked example

Runs in order: **FULL1 → DELTA1 → DELTA2 → FULL2 (latest)**

- After FULL2 completes, `archive_superseded_runs` sets `tags_archived=True` on FULL1,
  DELTA1, DELTA2; FULL2 stays active. Only **FULL2** shows the undo button.
- **Undo FULL2:**
  - `P` = FULL1; `restore_set` = {FULL1, DELTA1, DELTA2}.
  - Remove FULL2's managed tags not in the restore set; add the restore set's managed tags
    not currently live — net effect: original sessions revert to FULL1, the two new
    sessions revert to their DELTAs.
  - FULL2 `tags_archived=True`; FULL1, DELTA1, DELTA2 `tags_archived=False`.
  - FULL2 is now latest-FULL-but-archived → undo blocked; DELTAs were never directly
    undoable. Done.

# Part B — tag tooltip attribution

## Goal

The tag tooltip renders `Added by {{ tag.added_by }}` (`templates/annotations/tag_ui.html:17`),
computed in `TaggedModelMixin.prefetched_tags_json` (`apps/annotations/models.py:144`).
Eval-applied tags fall through to **"Participant"** because `tagging.py` creates
`CustomTaggedItem`s with no `user`. Fix this so an eval-driven tag reads:

    Added by evaluator '<evaluator name>' (run #<run id>)

## Data path

The live `CustomTaggedItem` has no FK to the evaluator/run; the **active** `AppliedTag`
(one whose run has `tags_archived=False` and is non-PREVIEW) is the source of truth.
Mapping a live tag on a target back to its `AppliedTag` is the inverse of `resolve_target`:

- **SESSION mode:** target is `session.chat` → `AppliedTag` via
  `evaluation_result.message.session.chat`.
- **message mode:** target is the `ChatMessage` → `AppliedTag` via
  `evaluation_result.message.expected_output_chat_message`.

Evaluator name = `AppliedTag.rule.evaluator.name`; run id =
`AppliedTag.evaluation_result.run_id`. Only rows whose run has `tags_archived=False` are
considered (so undo/supersede correctly changes attribution). Given the DELTA invariant,
the active set partitions targets, so at most one active `AppliedTag` applies a given tag
to a given target; if more ever coincide, pick the latest run.

## Steps

1. **Attribution helper** (`apps/annotations/prefetch.py` or `tagging.py`)
   - Given a set of targets (chats and/or chat-messages) + their tags, fetch active
     `AppliedTag`s (`select_related("rule__evaluator", "evaluation_result__run")`, filtered
     to `evaluation_result__run__tags_archived=False`) and build a map
     `{(content_type_id, object_id, tag_id): (evaluator_name, run_id)}`.
   - Attach the per-object slice as `prefetched_tag_attributions` (dict keyed by `tag_id`)
     alongside `prefetched_tagged_items`, in both `chat_tagged_items_prefetch()` /
     `attach_chat_tagged_items()` and the message/trace prefetch paths
     (`apps/trace/views.py:65`, `apps/experiments/views/experiment.py:624`,
     `apps/annotations/views/tag_views.py:170`).

2. **Enrich `prefetched_tags_json`** (`apps/annotations/models.py:144`)
   - When a tag has an attribution entry, set
     `added_by = f"evaluator '{name}' (run #{run_id})"`; otherwise keep the existing
     System / email / Participant logic. No template change needed.
   - Guard the lookup behind `hasattr(self, "prefetched_tag_attributions")` so
     non-enriched render paths degrade gracefully (no N+1).

3. **Tests**
   - SESSION-mode and message-mode eval tags show the evaluator + run id.
   - After undo (Part A), attribution flips to the restored run's evaluator/run (active
     `AppliedTag` changed); fully-removed tags drop out entirely.
   - Manual/user tags and system tags keep their existing `added_by` text.
   - Render paths without the attribution prefetch don't regress to N+1.

# Part C — ADR: only FULL runs are undoable

Write an ADR (per `AGENTS.md` → ADR workflow; copy `docs/adr/_template.md` to the next
free `docs/adr/NNNN-eval-tag-undo-full-runs-only.md`, add the index row + `mkdocs.yml`
nav entry). It must capture:

- **Decision:** Eval-driven tag undo operates only on the latest completed FULL run, once.
  DELTA and PREVIEW runs are never directly undoable.
- **Context:** The DELTA invariant — DELTA runs only ever add tags for disjoint, brand-new
  sessions (auto-population dedup), while FULL runs re-tag the whole dataset. This is what
  makes the run-level `tags_archived` flag and the run-set undo (restore = previous FULL +
  intervening DELTAs) correct, and is why a per-message walk-back is unnecessary.
- **Consequences:** Undo is a single-step revert of the latest tagging epoch; `tags_archived`
  on `EvaluationRun` records live state (state, not an event log — see #3488); the active
  tag set is always `{latest FULL} ∪ {later DELTAs}`.
- **Rejected alternatives:** per-row `AppliedTag.archived` + per-message walk-back (more
  state and queries for no behavioural gain under the invariant); undoing arbitrary
  historical runs (low value — fix the evaluator and re-run instead); making DELTAs
  directly undoable (meaningless — they supersede nothing).
