# Survey deprecation — Phase 1 design

**Status:** approved (brainstorm)
**Date:** 2026-06-10
**Tracking issue:** #3529 (to be repurposed onto the Feature-deprecation template)
**Process reference:** `docs/developer_guides/feature_deprecation.md`

## Summary

Begin removing the **Surveys** feature. Phase 1 severs the survey↔experiment
coupling immediately (owner has signed off on removal) and puts the standalone
Survey CRUD into read-only mode for a 30-day window so teams can copy the survey
URLs / confirmation text they configured before the model itself is removed in
Phase 2.

The original ticket (#3529) predates the deprecation process; this design
reconciles it with `feature_deprecation.md`.

## Tier & timeline

- **Tier:** full lifecycle (used), with feature-owner sign-off to remove — the
  removal-checkpoint triage is therefore pre-cleared.
- **Announced:** 2026-06-10.
- **Survey-model removal (Phase 2):** **2026-07-10** (30-day window). Below the
  documented 60-day minimum; acceptable here because of the owner sign-off, and
  noted as a divergence on the tracking issue.
- The window protects the **Survey records** (kept read-only so teams can export
  their data), *not* the experiment integration, which is severed in this phase.

## Scope

Phase 1 = sever experiment coupling **+** Survey CRUD read-only **+** day-0
announcement. The `Survey` model, nav link, table, and a read-only detail/edit
view all remain until Phase 2.

### Part A — Sever the experiment↔survey coupling

Code changes (no surveys can be attached to or triggered by an experiment after
this):

| Area | Change |
|---|---|
| `apps/experiments/models.py` | Remove `Experiment.pre_survey` / `post_survey` FK fields (state-only — see Migrations); remove the two survey entries from `Experiment._get_version_details()` (~L1011–1012); remove the two survey `_copy_attr_to_new_version` calls (~L898–899); delete `ExperimentSession.get_pre_survey_link` / `get_post_survey_link` (~L1452–1456); remove `SessionStatus.PENDING_PRE_SURVEY` (~L1329) |
| `apps/channels/channels_v2/stages/core.py` | Simplify `ConsentFlowStage`: consent transitions `PENDING → ACTIVE` directly; drop the pre-survey branch |
| `apps/experiments/views/experiment.py` | Remove `experiment_pre_survey` view (~L519–551); remove consent→pre-survey routing (~L460–465) and post-survey review logic (~L804–814) |
| `apps/experiments/urls.py` | Remove the `experiment_pre_survey` URL (~L105–107) |
| Templates | Delete `templates/experiments/pre_survey.html`; strip post-survey blocks from `templates/experiments/experiment_review.html` (~L30–32) and `templates/experiments/chat/chat_ui.html` (~L35); remove pre/post survey selectors from `templates/chatbots/settings_content.html` (~L159–167) |
| `apps/chatbots/forms.py` | Remove `pre_survey` / `post_survey` from `ChatbotSettingsForm` (~L62–63, L93–94) |
| `apps/experiments/forms.py` | Remove `SurveyCompletedForm` (~L35–36) |
| `apps/api/v2/inspect/versioning.py` | Drop `pre_survey` / `post_survey` from `select_related` (~L23–24) |
| `apps/teams/management/commands/clone_team.py` | Stop copying pre/post survey links onto cloned experiments. Survey *records* still clone (model stays in Phase 1) |
| `apps/utils/factories/experiment.py` | `ExperimentFactory` no longer creates a default `pre_survey` (~L67) |

### Part B — Survey CRUD goes read-only

Read-only enforcement per `feature_deprecation.md`:

- `CreateSurvey` view + "New survey" button — **blocked** (redirect or 403 with a
  deprecation message; button removed from templates).
- `EditSurvey` — rendered **view-only**: fields disabled, no save action, so
  teams can still read/copy `url` and `confirmation_text`.
- `DeleteSurvey` — **stays allowed** (helps drain usage).
- `SurveyHome`, `SurveyTableView`, the team-nav survey link, and `SurveyTable` —
  unchanged (records remain visible).

### Part C — Announcement (day 0)

Channels chosen: changelog/docs, in-feature warning, admin notification. (No
banner, no email.)

- **In-feature warning** — warning callout on the survey list + edit templates:
  *"Surveys are deprecated and will be removed on 2026-07-10. Please export any
  survey details you need before then."* No successor.
- **Admin notification** — new `survey_deprecation_notification(team)` in
  `apps/ocs_notifications/notifications.py`, mirroring
  `deprecated_model_notification` (uses `create_notification`, `WARNING` level,
  permission-scoped to survey managers). A one-off management command
  (`apps/.../management/commands/`) sends it to admins of every team with ≥1
  `Survey`.
- **Changelog + user-docs deprecation note** — lives in the separate
  [docs repo](https://github.com/dimagi/open-chat-studio-docs). Drafted here as
  text for a human to commit there (cannot commit cross-repo from this repo).

### Part D — Tests

Update / remove:
- `apps/experiments/tests/test_survey_views.py` — assert read-only behaviour
  (create blocked, edit view-only, delete allowed).
- `apps/experiments/tests/test_models.py` — survey-versioning / copy tests
  (~L677–735) updated for removed fields.
- `apps/channels/tests/test_base_channel_behavior.py` &
  `apps/channels/tests/channels/stages/test_consent_flow.py` — drop
  `PENDING_PRE_SURVEY` transition assertions; assert `PENDING → ACTIVE`.
- `apps/experiments/tests/test_session_access_cookie.py` (~L112–122) — remove
  pre-survey redirect assertions.
- `apps/teams/tests/test_clone_team.py` (~L51–62, L202–203) — surveys still
  clone, but experiments no longer carry survey links.
- `apps/utils/factories/experiment.py` — `SurveyFactory` kept; default
  `pre_survey` on `ExperimentFactory` removed.

Add:
- Data-migration test (PENDING_PRE_SURVEY sessions → ACTIVE; experiment survey
  columns nulled).
- `survey_deprecation_notification` test.
- Read-only `EditSurvey` / blocked `CreateSurvey` test.

## Migrations (deploy safety)

Dropping `pre_survey_id` / `post_survey_id` in the **same** deploy as the code
change is **not** backwards-compatible: Django emits explicit column lists in its
`SELECT`s, so during a rolling deploy the still-running old pods would crash the
moment the columns disappear. The physical column drop is therefore deferred to
Phase 2 (the two-phase drop the process prescribes).

**Phase 1 migrations (no DDL on the survey FK columns):**

1. **Data migration** —
   - `UPDATE` `pre_survey_id = NULL`, `post_survey_id = NULL` on **all**
     `Experiment` rows, including version rows. Backwards-compatible: nulling
     just makes still-running old pods behave like the new code (experiment with
     no pre-survey → `PENDING → ACTIVE`). It also guarantees no column references
     a `Survey` row, so `DeleteSurvey` during the read-only window cannot hit an
     FK violation (the field is gone from the model, so Django would no longer
     auto-`SET_NULL` it).
   - `UPDATE` `ExperimentSession.status = "active"` where
     `status = "pending-pre-survey"`, so no session is stranded on a status the
     new code no longer handles.
2. **State-only field removal** (`migrations.SeparateDatabaseAndState`) — Django
   drops the fields from model state; the columns physically remain. Old pods
   still selecting `pre_survey_id` continue to find it → no crash.

**Phase 2 migration (2026-07-10 deploy):** real `DROP COLUMN` for both
(`SeparateDatabaseAndState`, DB-only), bundled with dropping the `Survey` table.
By then all running code is post-Phase-1 and references neither.

Versioned-model note: `Experiment` is versioned. Removing the survey entries from
`_get_version_details` / `_copy_attr_to_new_version` is the Phase-1
versioned-model step (version-diff UI stops showing surveys; new snapshots stop
copying them). Nulling the columns on version rows is accepted data loss; the
`django-field-audit` log retains history. See
`docs/agents/django_model_versioning.md`.

## Tracking issue

Repurpose **#3529** onto `.github/ISSUE_TEMPLATE/feature_deprecation.md`:
- Front matter: Feature = Surveys; Surfaces = Survey model + experiment
  pre/post-survey FKs, survey CRUD UI/nav, `experiment_pre_survey` endpoint, user
  docs; Replacement = none; Tier = full lifecycle (used); Announced removal date
  = 2026-07-10.
- Usage audit: recorded as **known used** with feature-owner sign-off to remove;
  no fresh 90-day script is being run (the audit gates tier choice, and the tier
  + removal decision are already settled).
- Keep the full-lifecycle checklist; note the 30-day window and owner sign-off as
  divergences from the 60-day default and the removal-checkpoint triage.

## Out of scope — Phase 2 (after 2026-07-10)

Remove the `Survey` model, nav link, table, views, forms, permissions, and
`SurveyFactory`; `DROP COLUMN` the experiment FK columns; drop the `Survey`
table; remove user-docs pages (or leave a tombstone). Close #3529.
