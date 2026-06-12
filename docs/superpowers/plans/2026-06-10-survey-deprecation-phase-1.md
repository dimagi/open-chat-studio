# Survey Deprecation — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sever the survey↔experiment coupling, put Survey CRUD into read-only mode for a 30-day window, and announce the deprecation — Phase 1 of removing Surveys.

**Architecture:** Two-phase column drop (this PR removes the FK fields from Django *state* only; physical `DROP COLUMN` is deferred to Phase 2, mirroring migrations `0139`→`0140`). The `Survey` model, nav, table, and a read-only edit view survive until Phase 2 (2026-07-10). Runtime pre/post-survey behaviour is removed in this phase (owner sign-off).

**Tech Stack:** Django 5, pytest, FactoryBoy, django-field-audit, ruff, `ty`.

**Spec:** `docs/superpowers/specs/2026-06-10-survey-deprecation-phase-1-design.md`

**Conventions:** Module-level imports only (no in-function imports except to break import cycles). Lint/format/typecheck every changed file: `uv run ruff check <f> --fix && uv run ruff format <f> && uv run ty check apps/`.

---

## File map

**Modify (sever coupling):** `apps/chat/channels.py`, `apps/channels/channels_v2/stages/core.py`, `apps/experiments/decorators.py`, `apps/experiments/views/experiment.py`, `apps/experiments/views/__init__.py`, `apps/experiments/urls.py`, `apps/api/v2/inspect/versioning.py`, `apps/teams/management/commands/clone_team.py`, `templates/experiments/experiment_review.html`, `templates/experiments/chat/chat_ui.html`
**Modify (forms/factory):** `apps/chatbots/forms.py`, `apps/experiments/forms.py`, `apps/utils/factories/experiment.py`, `templates/chatbots/settings_content.html`
**Modify (model):** `apps/experiments/models.py`, `apps/experiments/model_audit_fields.py`
**Create (migrations):** `apps/experiments/migrations/0143_null_experiment_surveys.py`, `apps/experiments/migrations/0144_remove_experiment_survey_fields_state.py`
**Modify (read-only):** `apps/experiments/views/survey.py`
**Create (read-only templates):** `templates/experiments/survey_home.html`, `templates/experiments/survey_form.html`, `templates/experiments/components/survey_deprecation_warning.html`
**Create (notification):** `survey_deprecation_notification` in `apps/ocs_notifications/notifications.py`, `apps/experiments/management/commands/send_survey_deprecation_notifications.py`
**Delete:** `templates/experiments/pre_survey.html`
**Tests touched:** `apps/channels/tests/test_base_channel_behavior.py`, `apps/channels/tests/channels/stages/test_consent_flow.py`, `apps/teams/tests/test_clone_team.py`, `apps/experiments/tests/test_survey_views.py`, `apps/experiments/tests/test_views.py`, `apps/experiments/tests/test_models.py`, `apps/experiments/tests/test_versioning.py`, `apps/experiments/tests/test_session_access_cookie.py`, `apps/events/tests/test_timeout_trigger.py`

> **Ordering rationale:** `SessionStatus.PENDING_PRE_SURVEY` is referenced in a view decorator's `allowed_states=[...]` (evaluated at import). So all *usages* of the enum value and the survey links are removed first (Tasks 1–2) while the model fields + enum value still exist; the enum value, methods, and fields are removed last (Task 3). Each task leaves the suite importable and green.

---

## Task 1: Sever runtime survey usage (channels, routing, views, URLs)

Remove every *use* of `pre_survey`/`post_survey`/`PENDING_PRE_SURVEY` outside the model layer. The model fields and enum value still exist after this task.

**Files:**
- Modify: `apps/chat/channels.py`, `apps/channels/channels_v2/stages/core.py`, `apps/experiments/decorators.py`, `apps/experiments/views/experiment.py`, `apps/experiments/views/__init__.py`, `apps/experiments/urls.py`, `apps/api/v2/inspect/versioning.py`, `apps/teams/management/commands/clone_team.py`
- Modify templates: `templates/experiments/experiment_review.html`, `templates/experiments/chat/chat_ui.html`
- Delete: `templates/experiments/pre_survey.html`
- Test: `apps/channels/tests/test_base_channel_behavior.py`, `apps/channels/tests/channels/stages/test_consent_flow.py`, `apps/experiments/tests/test_session_access_cookie.py`

- [ ] **Step 1: Update consent-flow tests to expect direct activation (failing)**

In `apps/channels/tests/channels/stages/test_consent_flow.py`:
- Delete `test_pending_consent_with_survey` (lines ~93–109) and `test_pre_survey_consent_activates` (lines ~111+).
- In the `_make_experiment` helper (line ~20), remove the `pre_survey` parameter and the `experiment.pre_survey = pre_survey` line (~24).
- Update `test_pending_consent_no_survey_activates` (line ~79) to call `self._make_experiment(seed_message=None)`.

In `apps/channels/tests/test_base_channel_behavior.py`:
- Remove the pre-survey assertions/flow at lines ~288–304 and ~315, ~323, ~512–513. Replace the "consent → PENDING_PRE_SURVEY → ACTIVE" expectations with "consent → ACTIVE". (Read the surrounding test to retarget its assertions; the new expectation is `channel.experiment_session.status == SessionStatus.ACTIVE` immediately after consent.)

In `apps/experiments/tests/test_session_access_cookie.py` (lines ~112–114): remove the `if session.experiment.pre_survey:` branch that expects a redirect to `experiments:experiment_pre_survey`.

- [ ] **Step 2: Run the consent-flow tests to verify they fail**

Run: `uv run pytest apps/channels/tests/channels/stages/test_consent_flow.py -v`
Expected: FAIL (code still routes to `PENDING_PRE_SURVEY`).

- [ ] **Step 3: Simplify the v2 consent stage**

In `apps/channels/channels_v2/stages/core.py`:

Replace the `should_run` status list (remove `PENDING_PRE_SURVEY`):
```python
            and ctx.experiment_session.status
            in [
                SessionStatus.SETUP,
                SessionStatus.PENDING,
            ]
```
Replace the `process` body's PENDING/PRE_SURVEY branches with a single direct activation:
```python
        if session.status == SessionStatus.SETUP:
            session.update_status(SessionStatus.PENDING)
            response = self._build_consent_prompt(ctx)

        elif session.status == SessionStatus.PENDING:
            if self._user_gave_consent(ctx):
                response = self._start_conversation(ctx)
            else:
                response = self._build_consent_prompt(ctx)

        if response is not None:
            raise EarlyExitResponse(response)
```
Delete the `_build_survey_prompt` method (lines ~335–339).

- [ ] **Step 4: Simplify the v1 channel pre-conversation flow**

In `apps/chat/channels.py`, replace `_handle_pre_conversation_requirements` (lines ~460–489) with:
```python
    def _handle_pre_conversation_requirements(self) -> str | None:
        """External channels lack a UI, so consent is collected via the conversation thread.

        Session started -> status SETUP
        (SETUP) first user message -> status PENDING, ask for consent
        (PENDING) user gave consent -> status ACTIVE, start conversation
        """
        if self.experiment_session.status == SessionStatus.SETUP:
            return self._chat_initiated()
        elif self.experiment_session.status == SessionStatus.PENDING:
            if self._user_gave_consent():
                return self.start_conversation()
            else:
                return self._ask_user_for_consent()
        return None
```
Delete `_ask_user_to_take_survey` (lines ~524–530). In `_should_handle_pre_conversation_requirements` (lines ~537–541) remove the `SessionStatus.PENDING_PRE_SURVEY` list entry, leaving `[SessionStatus.SETUP, SessionStatus.PENDING]`.

- [ ] **Step 5: Remove pre-survey routing from session redirect + decorators**

In `apps/experiments/decorators.py`, delete the case (lines ~135–136):
```python
        case SessionStatus.PENDING_PRE_SURVEY:
            return HttpResponseRedirect(reverse("experiments:experiment_pre_survey", args=view_args))
```

In `apps/experiments/views/experiment.py`, change `_record_consent_and_redirect` (lines ~458–466) to always activate:
```python
    # record consent, update status
    experiment_session.consent_date = timezone.now()
    experiment_session.status = SessionStatus.ACTIVE
    experiment_session.save()
    response = HttpResponseRedirect(
        reverse(
            "chatbots:chatbot_chat",
            args=[team_slug, experiment_session.experiment.public_id, experiment_session.external_id],
        )
    )
    return set_session_access_cookie(response, experiment, experiment_session)
```
Delete the entire `experiment_pre_survey` view (lines ~517–551, including its `@experiment_session_view` / `@verify_session_access_cookie` decorators).

Simplify `experiment_review` (lines ~789–814): remove the `survey_link`/`survey_text` locals, the `elif experiment_version.post_survey:` branch, and the `"experiment.post_survey"`, `"survey_link"`, `"survey_text"` keys from `version_specific_vars`. Result:
```python
def experiment_review(request, team_slug: str, experiment_id: uuid.UUID, session_id: str):
    form = None
    experiment_version = resolve_published_or_working(request.experiment)
    if request.method == "POST":
        # no validation needed
        request.experiment_session.status = SessionStatus.COMPLETE
        request.experiment_session.reviewed_at = timezone.now()
        request.experiment_session.save()
        return HttpResponseRedirect(
            reverse("experiments:experiment_complete", args=[team_slug, experiment_id, session_id])
        )

    version_specific_vars = {
        "experiment_name": experiment_version.name,
    }
```
Remove the now-unused `SurveyCompletedForm` from the `apps.experiments.forms` import in this file.

- [ ] **Step 6: Remove the URL, view export, inspect select_related, clone remap**

`apps/experiments/urls.py`: delete the `experiment_pre_survey` path (lines ~104–108).
`apps/experiments/views/__init__.py`: remove `experiment_pre_survey` from the import (line ~17).
`apps/api/v2/inspect/versioning.py`: delete `"pre_survey",` and `"post_survey",` from the `select_related` list (lines ~23–24).
`apps/teams/management/commands/clone_team.py`: delete the pre/post-survey remap block (lines ~440–454). Leave the Survey-cloning block (~343) intact — surveys still exist in Phase 1.

- [ ] **Step 7: Update templates**

`templates/experiments/experiment_review.html`: replace the `{% if survey_link %}...{% else %}...{% endif %}` block (lines ~30–37) with just the no-survey content:
```html
      <p>
        Please click the button below to end the experiment and submit your chat details.
      </p>
```
`templates/experiments/chat/chat_ui.html` (line ~35): change `{% if experiment.consent_form or experiment.post_survey %}` to `{% if experiment.consent_form %}`.
Delete `templates/experiments/pre_survey.html`.

- [ ] **Step 8: Run the affected suites to green**

Run: `uv run pytest apps/channels/tests/channels/stages/test_consent_flow.py apps/channels/tests/test_base_channel_behavior.py apps/experiments/tests/test_session_access_cookie.py -v`
Expected: PASS.

- [ ] **Step 9: Lint, format, typecheck changed files**

Run (set `FILES` once, reuse for check + format):
```bash
FILES="apps/chat/channels.py apps/channels/channels_v2/stages/core.py apps/experiments/decorators.py apps/experiments/views/experiment.py apps/experiments/views/__init__.py apps/experiments/urls.py apps/api/v2/inspect/versioning.py apps/teams/management/commands/clone_team.py"
uv run ruff check $FILES --fix && uv run ruff format $FILES && uv run ty check apps/
```
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add apps/chat/channels.py apps/channels/ apps/experiments/decorators.py apps/experiments/views/ apps/experiments/urls.py apps/api/v2/inspect/versioning.py apps/teams/management/commands/clone_team.py templates/experiments/ apps/experiments/tests/test_session_access_cookie.py
git rm templates/experiments/pre_survey.html
git commit -m "Remove pre/post-survey runtime coupling from experiments

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Remove survey form fields, factory default, settings UI

**Files:**
- Modify: `apps/chatbots/forms.py`, `apps/experiments/forms.py`, `apps/utils/factories/experiment.py`, `templates/chatbots/settings_content.html`
- Test: `apps/experiments/tests/test_views.py`

- [ ] **Step 1: Drop the survey fields from the chatbot settings form**

`apps/chatbots/forms.py`: remove `"pre_survey",` and `"post_survey",` from `Meta.fields` (lines ~62–63), and delete the two queryset lines (~93–94):
```python
        self.fields["pre_survey"].queryset = team.survey_set.exclude(is_version=True)
        self.fields["post_survey"].queryset = team.survey_set.exclude(is_version=True)
```

- [ ] **Step 2: Remove SurveyCompletedForm**

`apps/experiments/forms.py`: delete the `SurveyCompletedForm` class (lines ~35–36). (Its only users were removed in Task 1.)

- [ ] **Step 3: Remove the pre_survey default from ExperimentFactory**

`apps/utils/factories/experiment.py`: delete line ~67:
```python
    pre_survey = factory.SubFactory(SurveyFactory, team=factory.SelfAttribute("..team"))
```
Keep `SurveyFactory` (still used by survey + clone tests).

- [ ] **Step 4: Remove the survey selectors from the settings template**

`templates/chatbots/settings_content.html`: delete the entire `#surveys-section` block (lines ~155–171).

- [ ] **Step 5: Fix the experiment-session factory kwarg in tests**

`apps/experiments/tests/test_views.py` (line ~513): change `ExperimentSessionFactory.create(experiment__pre_survey=None)` to `ExperimentSessionFactory.create()`.

- [ ] **Step 6: Run affected suites**

Run: `uv run pytest apps/chatbots apps/experiments/tests/test_views.py -q`
Expected: PASS.

- [ ] **Step 7: Lint/format/typecheck + commit**

Run:
```bash
FILES="apps/chatbots/forms.py apps/experiments/forms.py apps/utils/factories/experiment.py"
uv run ruff check $FILES --fix && uv run ruff format $FILES && uv run ty check apps/
```
```bash
git add apps/chatbots/forms.py apps/experiments/forms.py apps/utils/factories/experiment.py templates/chatbots/settings_content.html apps/experiments/tests/test_views.py
git commit -m "Remove survey fields from chatbot settings form and factory

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Remove model coupling + two-phase migration

Now the model fields, the `PENDING_PRE_SURVEY` enum value, the survey link methods, version wiring, and audit entry are removed. The DB columns are kept (state-only removal); data is nulled and pending sessions migrated.

**Files:**
- Modify: `apps/experiments/models.py`, `apps/experiments/model_audit_fields.py`
- Create: `apps/experiments/migrations/0143_null_experiment_surveys.py`, `apps/experiments/migrations/0144_remove_experiment_survey_fields_state.py`
- Test: `apps/experiments/tests/test_models.py`, `apps/experiments/tests/test_versioning.py`, `apps/events/tests/test_timeout_trigger.py`

- [ ] **Step 1: Write the data migration (null columns + migrate sessions)**

Create `apps/experiments/migrations/0143_null_experiment_surveys.py`:
```python
from django.db import migrations


class Migration(migrations.Migration):
    """Null the experiment pre/post-survey FK columns and move any sessions
    stuck in 'pending-pre-survey' to 'active', ahead of removing the fields.

    Nulling is backwards-compatible: it makes still-running pre-deploy code
    behave like the new code (no pre-survey -> PENDING goes straight to ACTIVE),
    and leaves no column referencing a Survey row, so survey deletes during the
    read-only window cannot hit an FK violation.
    """

    dependencies = [
        ("experiments", "0142_remove_experiment_use_processor_bot_voice"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "UPDATE experiments_experiment SET pre_survey_id = NULL, post_survey_id = NULL "
                "WHERE pre_survey_id IS NOT NULL OR post_survey_id IS NOT NULL;",
                "UPDATE experiments_experimentsession SET status = 'active' "
                "WHERE status = 'pending-pre-survey';",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
```

- [ ] **Step 2: Apply it to verify it runs**

Run: `uv run python manage.py migrate experiments 0143`
Expected: applies cleanly. Then `uv run python manage.py migrate experiments` to return to head before continuing (or stay; later migrations don't conflict).

- [ ] **Step 3: Update versioning/timeout tests to drop survey expectations (failing)**

`apps/experiments/tests/test_models.py`: in the version-details test (lines ~677–736), remove the `pre_survey`/`post_survey` setup (678–681), the `"pre_survey"`/`"post_survey"` entries in the expected field list (722–723), and the two `self._assert_attribute_duplicated("pre_survey"/"post_survey", ...)` calls (735–736).
`apps/experiments/tests/test_versioning.py` (lines ~297–298): remove the `experiment_copy.pre_survey == experiment.pre_survey` assertions.
`apps/events/tests/test_timeout_trigger.py` (line ~328): remove the `(SessionStatus.PENDING_PRE_SURVEY, True),` parametrize case.

- [ ] **Step 4: Run those tests to verify they fail**

Run: `uv run pytest apps/experiments/tests/test_models.py -k version -v`
Expected: FAIL (model still has the fields / version entries).

- [ ] **Step 5: Remove the model-level coupling**

In `apps/experiments/models.py`:
- `Survey.archive()` (lines ~252–256) — remove the two reverse-relation updates:
```python
    @transaction.atomic()
    def archive(self):
        super().archive()
```
- Delete the `pre_survey` and `post_survey` fields (lines ~561–566).
- Remove the two survey `_copy_attr_to_new_version` calls (lines ~898–899).
- Remove the `# Surveys` group VersionFields in `_get_version_details` (lines ~1010–1012).
- Remove `PENDING_PRE_SURVEY = "pending-pre-survey", gettext("Awaiting pre-survey")` from `SessionStatus` (line ~1329).
- Delete `get_pre_survey_link` and `get_post_survey_link` from `ExperimentSession` (lines ~1452–1456).

`apps/experiments/model_audit_fields.py`: remove `"pre_survey",` and `"post_survey",` from `EXPERIMENT_FIELDS` (lines ~5–6).

- [ ] **Step 6: Generate + reshape the state-only / choices migration**

Run: `uv run python manage.py makemigrations experiments --name remove_experiment_survey_fields_state`

Django will emit `RemoveField(pre_survey)`, `RemoveField(post_survey)` (which would DROP the columns) and an `AlterField` on `experimentsession.status` (choices change). Edit the generated `0144_remove_experiment_survey_fields_state.py` so the RemoveFields are state-only (keep columns) while the choices AlterField runs normally:
```python
from django.db import migrations, models


class Migration(migrations.Migration):
    """Remove pre_survey/post_survey from Django state, leaving the columns in
    place (dropped in Phase 2, mirroring 0139 -> 0140). Also drops the
    'pending-pre-survey' choice from ExperimentSession.status (no DB change).
    """

    dependencies = [
        ("experiments", "0143_null_experiment_surveys"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="experiment", name="pre_survey"),
                migrations.RemoveField(model_name="experiment", name="post_survey"),
            ],
            database_operations=[],
        ),
        migrations.AlterField(
            model_name="experimentsession",
            name="status",
            field=models.CharField(
                choices=[
                    ("setup", "Setting Up"),
                    ("pending", "Awaiting participant"),
                    ("active", "Active"),
                    ("pending-review", "Awaiting final review."),
                    ("complete", "Complete"),
                    ("unknown", "Unknown"),
                ],
                default="setup",
                max_length=20,
            ),
        ),
    ]
```
(If `makemigrations` produced the `AlterField` with the exact current choice list, copy that list verbatim rather than the one above.)

- [ ] **Step 7: Verify migration state is consistent (no pending changes)**

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: "No changes detected".
Run: `uv run python manage.py migrate`
Expected: applies cleanly.

- [ ] **Step 8: Run the model/version/timeout tests to green**

Run: `uv run pytest apps/experiments/tests/test_models.py apps/experiments/tests/test_versioning.py apps/events/tests/test_timeout_trigger.py -q`
Expected: PASS.

- [ ] **Step 9: Lint/format/typecheck + commit**

Run:
```bash
FILES="apps/experiments/models.py apps/experiments/model_audit_fields.py apps/experiments/migrations/0143_null_experiment_surveys.py apps/experiments/migrations/0144_remove_experiment_survey_fields_state.py"
uv run ruff check $FILES --fix && uv run ruff format $FILES && uv run ty check apps/
```
```bash
git add apps/experiments/models.py apps/experiments/model_audit_fields.py apps/experiments/migrations/0143_null_experiment_surveys.py apps/experiments/migrations/0144_remove_experiment_survey_fields_state.py apps/experiments/tests/
git commit -m "Remove survey fields and PENDING_PRE_SURVEY from experiment models (state-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Survey CRUD read-only

Block create, make edit view-only, hide the "New" button, add the deprecation warning. Delete stays allowed.

**Files:**
- Modify: `apps/experiments/views/survey.py`
- Create: `templates/experiments/components/survey_deprecation_warning.html`, `templates/experiments/survey_home.html`, `templates/experiments/survey_form.html`
- Test: `apps/experiments/tests/test_survey_views.py`

- [ ] **Step 1: Rewrite the survey-views tests for read-only behaviour (failing)**

Replace the FK-coupling tests in `apps/experiments/tests/test_survey_views.py` (the existing tests assert `experiment.pre_survey`/`post_survey` and archive-nulling — both gone). New tests:
```python
import pytest
from django.urls import reverse

from apps.experiments.models import Survey
from apps.utils.factories.experiment import SurveyFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def admin_user(team):
    return team.members.first()


def test_create_survey_is_blocked(client, team, admin_user):
    client.force_login(admin_user)
    url = reverse("experiments:survey_new", args=[team.slug])
    response = client.get(url)
    assert response.status_code == 302
    assert response.url == reverse("experiments:survey_home", args=[team.slug])
    assert Survey.objects.filter(team=team).count() == 0


def test_edit_survey_is_read_only(client, team, admin_user):
    client.force_login(admin_user)
    survey = SurveyFactory.create(team=team, name="Original")
    url = reverse("experiments:survey_edit", args=[team.slug, survey.id])

    get_response = client.get(url)
    assert get_response.status_code == 200
    form = get_response.context["form"]
    assert all(field.disabled for field in form.fields.values())

    post_response = client.post(url, {"name": "Changed", "url": survey.url, "confirmation_text": "x"})
    assert post_response.status_code == 302
    survey.refresh_from_db()
    assert survey.name == "Original"


def test_delete_survey_still_allowed(client, team, admin_user):
    client.force_login(admin_user)
    survey = SurveyFactory.create(team=team)
    url = reverse("experiments:survey_delete", args=[team.slug, survey.id])
    response = client.delete(url)
    assert response.status_code == 200
    survey.refresh_from_db()
    assert survey.is_archived is True
```
(Confirm `TeamWithUsersFactory` exists in `apps/utils/factories/team.py`; if the member lacks survey perms, grant them or use the project's standard authed-client fixture from `apps/conftest.py`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest apps/experiments/tests/test_survey_views.py -v`
Expected: FAIL (create succeeds, edit saves).

- [ ] **Step 3: Make the views read-only**

Rewrite `apps/experiments/views/survey.py`:
```python
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.experiments.forms import SurveyForm
from apps.experiments.models import Survey
from apps.experiments.tables import SurveyTable
from apps.generics.help import render_help_with_link
from apps.teams.mixins import LoginAndTeamRequiredMixin

SURVEY_DEPRECATION_MESSAGE = (
    "Surveys are deprecated and will be removed on 2026-07-10. "
    "New surveys can no longer be created."
)


class SurveyHome(LoginAndTeamRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "experiments/survey_home.html"
    permission_required = "experiments.view_survey"

    def get_context_data(self, team_slug: str, **kwargs):  # ty: ignore[invalid-method-override]
        return {
            "active_tab": "survey",
            "title": "Survey",
            "title_help_content": render_help_with_link("", "survey"),
            "allow_new": False,
            "table_url": reverse("experiments:survey_table", args=[team_slug]),
        }


class SurveyTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    model = Survey
    table_class = SurveyTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_survey"

    def get_queryset(self):
        return Survey.objects.filter(team=self.request.team, is_version=False)


class CreateSurvey(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    """Survey creation is disabled during deprecation."""

    permission_required = "experiments.add_survey"

    def dispatch(self, request, team_slug: str, *args, **kwargs):
        messages.error(request, SURVEY_DEPRECATION_MESSAGE)
        return HttpResponseRedirect(reverse("experiments:survey_home", args=[team_slug]))


class EditSurvey(LoginAndTeamRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Survey
    form_class = SurveyForm
    template_name = "experiments/survey_form.html"
    extra_context = {
        "title": "View Survey",
        "page_title": "View Survey",
        "active_tab": "survey",
    }
    permission_required = "experiments.view_survey"

    def get_queryset(self):
        return Survey.objects.filter(team=self.request.team)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.disabled = True
        return form

    def post(self, request, *args, **kwargs):
        messages.error(request, "Surveys are read-only and can no longer be edited.")
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse("experiments:survey_home", args=[self.request.team.slug])


class DeleteSurvey(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "experiments.delete_survey"

    def delete(self, request, team_slug: str, pk: int):
        survey = get_object_or_404(Survey, id=pk, team=request.team)
        survey.archive()
        messages.success(request, "Survey Deleted")
        return HttpResponse()
```
Note `EditSurvey.permission_required` drops to `view_survey` so read-only viewers can open it.

- [ ] **Step 4: Create the deprecation-warning include**

Create `templates/experiments/components/survey_deprecation_warning.html`:
```html
<div class="alert alert-warning my-2" role="alert">
  <i class="fa-solid fa-triangle-exclamation"></i>
  <span>
    Surveys are deprecated and will be removed on <strong>2026-07-10</strong>.
    Please export any survey details you need before then.
  </span>
</div>
```

- [ ] **Step 5: Create the survey home + form templates**

Create `templates/experiments/survey_home.html`:
```html
{% extends "web/app/app_base.html" %}
{% block app %}
  {% include "experiments/components/survey_deprecation_warning.html" %}
  {% include "generic/object_home_content.html" %}
{% endblock app %}
```
Create `templates/experiments/survey_form.html`:
```html
{% extends "generic/object_form.html" %}
{% block pre_form %}
  {% include "experiments/components/survey_deprecation_warning.html" %}
{% endblock pre_form %}
{% block form_actions %}{% endblock form_actions %}
```

- [ ] **Step 6: Run the survey-view tests to green**

Run: `uv run pytest apps/experiments/tests/test_survey_views.py -v`
Expected: PASS.

- [ ] **Step 7: Lint/format/typecheck + commit**

Run: `uv run ruff check apps/experiments/views/survey.py --fix && uv run ruff format apps/experiments/views/survey.py && uv run ty check apps/`
```bash
git add apps/experiments/views/survey.py templates/experiments/survey_home.html templates/experiments/survey_form.html templates/experiments/components/survey_deprecation_warning.html apps/experiments/tests/test_survey_views.py
git commit -m "Make survey CRUD read-only with deprecation warning

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Deprecation notification + management command

**Files:**
- Modify: `apps/ocs_notifications/notifications.py`
- Create: `apps/experiments/management/commands/send_survey_deprecation_notifications.py`
- Test: `apps/ocs_notifications/tests/test_survey_deprecation_notification.py` (create)

- [ ] **Step 1: Write the notification test (failing)**

Create `apps/ocs_notifications/tests/test_survey_deprecation_notification.py`:
```python
import pytest

from apps.ocs_notifications.models import NotificationEvent
from apps.ocs_notifications.notifications import survey_deprecation_notification
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
def test_survey_deprecation_notification_creates_event():
    team = TeamWithUsersFactory.create()
    survey_deprecation_notification(team)
    event = NotificationEvent.objects.filter(team=team).first()
    assert event is not None
    assert "2026-07-10" in event.message
    assert "deprecated" in event.message.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest apps/ocs_notifications/tests/test_survey_deprecation_notification.py -v`
Expected: FAIL with `ImportError` (function not defined).

- [ ] **Step 3: Add the notification function**

Append to `apps/ocs_notifications/notifications.py`:
```python
@silence_exceptions(logger, log_message="Failed to create survey deprecation notification")
def survey_deprecation_notification(team) -> None:
    """Notify a team that the Surveys feature is being removed."""
    create_notification(
        title="Surveys are being removed",
        message=(
            "The Surveys feature is deprecated and will be removed on 2026-07-10. "
            "Surveys are now read-only and are no longer connected to chatbots. "
            "Please export any survey details you need before then."
        ),
        level=LevelChoices.WARNING,
        team=team,
        slug="survey-feature-deprecated",
        permissions=["experiments.change_survey"],
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest apps/ocs_notifications/tests/test_survey_deprecation_notification.py -v`
Expected: PASS.

- [ ] **Step 5: Add the management command**

Create `apps/experiments/management/commands/send_survey_deprecation_notifications.py`:
```python
from django.core.management.base import BaseCommand

from apps.experiments.models import Survey
from apps.ocs_notifications.notifications import survey_deprecation_notification
from apps.teams.models import Team


class Command(BaseCommand):
    help = "Send the one-off survey-deprecation notification to admins of teams with surveys."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List affected teams without notifying.")

    def handle(self, *args, **options):
        team_ids = (
            Survey.objects.filter(is_version=False).values_list("team_id", flat=True).distinct()
        )
        teams = Team.objects.filter(id__in=list(team_ids))
        self.stdout.write(f"{teams.count()} team(s) with surveys.")
        if options["dry_run"]:
            for team in teams:
                self.stdout.write(f"  would notify: {team.slug}")
            return
        for team in teams:
            survey_deprecation_notification(team)
            self.stdout.write(f"  notified: {team.slug}")
```

- [ ] **Step 6: Smoke-test the command**

Run: `uv run python manage.py send_survey_deprecation_notifications --dry-run`
Expected: prints a team count without error.

- [ ] **Step 7: Lint/format/typecheck + commit**

Run:
```bash
FILES="apps/ocs_notifications/notifications.py apps/experiments/management/commands/send_survey_deprecation_notifications.py apps/ocs_notifications/tests/test_survey_deprecation_notification.py"
uv run ruff check $FILES --fix && uv run ruff format $FILES && uv run ty check apps/
```
```bash
git add apps/ocs_notifications/notifications.py apps/experiments/management/commands/send_survey_deprecation_notifications.py apps/ocs_notifications/tests/test_survey_deprecation_notification.py
git commit -m "Add survey deprecation notification and command

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Changelog / docs deprecation note (draft for docs repo)

The user docs live in the separate `open-chat-studio-docs` repo; this repo can't commit there. Produce the text for a human to paste.

**Files:**
- Create: `docs/superpowers/specs/survey-deprecation-docs-note.md` (draft only; not the live doc)

- [ ] **Step 1: Write the draft note**

Create `docs/superpowers/specs/survey-deprecation-docs-note.md`:
```markdown
# Draft for open-chat-studio-docs (changelog + Surveys page note)

## Changelog entry
**Surveys are being removed.** Surveys are now read-only and are no longer
connected to chatbots (pre-/post-survey settings have been removed from chatbot
configuration). The Surveys feature will be removed entirely on **2026-07-10**.
Export any survey details you need before then. Questions or objections: open an
issue on dimagi/open-chat-studio (#3529) or contact support.

## Surveys page deprecation callout
> **Deprecated.** This feature will be removed on 2026-07-10. Surveys can no
> longer be created or edited and are no longer attached to chatbots.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/survey-deprecation-docs-note.md
git commit -m "Add docs-repo deprecation note draft

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Repurpose tracking issue #3529

**Files:** none (GitHub).

- [ ] **Step 1: Rewrite the issue onto the deprecation template**

Use the body below (front matter + full-lifecycle checklist, per `.github/ISSUE_TEMPLATE/feature_deprecation.md`), noting the divergences. Write it to a temp file and apply with `gh`:
```bash
gh issue edit 3529 --repo dimagi/open-chat-studio \
  --title "Deprecate: Surveys" \
  --add-label maintenance \
  --body-file /tmp/3529-body.md
```
`/tmp/3529-body.md` content:
```markdown
**Feature:** Surveys
**Surfaces affected:** `Survey` model + experiment `pre_survey`/`post_survey` FKs; survey CRUD UI/nav/table; `experiment_pre_survey` endpoint + pre-survey chat flow; user docs
**Replacement:** none
**Usage audit:** known used; feature-owner sign-off to remove (no fresh 90-day script run)
**Tier:** full lifecycle (used)
**Announced removal date:** 2026-07-10 (30-day window — below the 60-day default; accepted given owner sign-off)

## Full lifecycle (used)
- [x] Audit: known used; owner sign-off recorded above
- [x] Day 0 announce: in-feature warning on survey pages; in-product notification to affected team admins; changelog + user-docs note drafted for the docs repo (banner + email intentionally skipped)
- [x] Read-only mode: survey create blocked, edit view-only, delete allowed; experiment coupling severed (Phase 1 PR)
- [ ] Deprecation window (to 2026-07-10): support export requests
- [x] Removal checkpoint: pre-cleared by owner sign-off
- [ ] Phase 2 — drop `Survey` model, nav, table, views, forms, permissions; `DROP COLUMN` the experiment FK columns; drop the `Survey` table
- [ ] Close out: remove/redirect user docs pages; close this issue

Plan: `docs/superpowers/plans/2026-06-10-survey-deprecation-phase-1.md`
```
(If `gh issue edit` fails with a GraphQL error, fall back to the REST API: `gh api -X PATCH repos/dimagi/open-chat-studio/issues/3529 -f title=... -f body=@/tmp/3529-body.md`.)

---

## Task 8: Final verification

- [ ] **Step 1: Migration consistency**

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: "No changes detected".

- [ ] **Step 2: Full grep for stragglers**

Run: `grep -rn "pre_survey\|post_survey\|PENDING_PRE_SURVEY\|pending-pre-survey\|SurveyCompletedForm\|get_pre_survey_link\|get_post_survey_link" apps/ templates/ --include=*.py --include=*.html | grep -v "/migrations/"`
Expected: no matches outside Task 6's draft note. (Surveys app code — `Survey` model, `SurveyForm`, `SurveyFactory`, `SurveyTable` — legitimately remains.)

- [ ] **Step 3: Run the broad suites touched**

Run: `uv run pytest apps/experiments apps/channels apps/chatbots apps/ocs_notifications apps/teams apps/events -q`
Expected: PASS.

- [ ] **Step 4: Typecheck the project**

Run: `uv run ty check apps/`
Expected: no errors.

---

## Self-review notes (addressed)

- **`Survey.archive()` reverse relations** — removed in Task 3 Step 5 (they break once the FK leaves model state; columns are already nulled so there is nothing to clear).
- **`PENDING_PRE_SURVEY` in a view decorator's `allowed_states`** — the view is deleted in Task 1 before the enum value is removed in Task 3 (import-time safety).
- **`status` choices migration** — handled as a normal `AlterField` (no DB change) alongside the state-only field removal in Task 3 Step 6.
- **Deploy safety** — columns kept this PR (state-only); physical `DROP COLUMN` is Phase 2, mirroring `0139`→`0140`.
- **Survey deletes during the window** — safe: Task 3's data migration nulls all FK columns, so no row references a `Survey`.
