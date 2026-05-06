# Participant Create View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Django form view to create a single participant by hand, accessible from the participants home page.

**Architecture:** Repurpose the unwired `CreateParticipant` `CreateView` at `apps/participants/views.py:110`, replace the broken `ParticipantForm` with a working ModelForm that validates uniqueness, register the URL via the existing `make_crud_urls` helper, and add a "Create" action button on the participants home page.

**Tech Stack:** Django (ModelForm, CreateView, PermissionRequiredMixin), pytest-django, factory_boy. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-05-05-participant-create-view-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `apps/participants/forms.py` | `ParticipantForm` — ModelForm with team-scoped uniqueness validation and platform choice list matching the CSV importer | Modify (rewrite `ParticipantForm`) |
| `apps/participants/views.py` | `CreateParticipant` view — wire up team in form kwargs, drop broken `created_by`, redirect to detail page; `ParticipantHome` adds Create action | Modify |
| `apps/participants/urls.py` | URL config — flip `new=False` to `new=True` so `participants/new/` resolves | Modify |
| `apps/participants/tests/test_forms.py` | Tests for `ParticipantForm` (validation, platform choices, duplicate detection) | Create |
| `apps/participants/tests/test_views.py` | Tests for `CreateParticipant` view (GET, POST success, POST duplicate, home page action) | Modify |

---

## Task 1: Replace ParticipantForm with a working ModelForm (TDD)

**Files:**
- Create: `apps/participants/tests/test_forms.py`
- Modify: `apps/participants/forms.py:13-19` (replace `ParticipantForm`)

### Background for the engineer

The current `ParticipantForm` in `apps/participants/forms.py` is broken for create: `identifier` is `disabled=True`, and only `user` is editable. It has no `team` awareness. We're replacing it with a form that:

1. Exposes `identifier`, `platform`, `name` (name optional).
2. Populates the `platform` dropdown using `ChannelPlatform.for_dropdown(used_platforms=[], team=team)` keys plus `WEB` and `API` — matches the set the CSV importer accepts (see `apps/participants/import_export.py:31-33`).
3. In `clean()`, checks for an existing `(team, platform, identifier)` row and raises a non-field `ValidationError` whose message contains an HTML link to the existing participant's detail page.

We use `format_html` (already safe-by-default for `ValidationError` rendering when surfaced via `{{ form.non_field_errors }}` in templates that mark them safe — `generic/object_form.html` does so).

`ParticipantFactory` lives at `apps/utils/factories/experiment.py:103`. By default it does **not** set `platform`, so tests should pass `platform=` explicitly when the test relies on the unique constraint.

- [ ] **Step 1.1: Write failing tests for `ParticipantForm`**

Create `apps/participants/tests/test_forms.py` with this content:

```python
import pytest

from apps.channels.models import ChannelPlatform
from apps.experiments.models import Participant
from apps.participants.forms import ParticipantForm
from apps.utils.factories.experiment import ParticipantFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_participant_form_creates_participant():
    team = TeamFactory.create()
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.WEB, "name": "Alice"},
        team=team,
    )
    assert form.is_valid(), form.errors
    participant = form.save(commit=False)
    participant.team = team
    participant.save()
    assert participant.identifier == "user@example.com"
    assert participant.platform == ChannelPlatform.WEB
    assert participant.name == "Alice"


@pytest.mark.django_db()
def test_participant_form_name_is_optional():
    team = TeamFactory.create()
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.WEB},
        team=team,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_participant_form_requires_identifier_and_platform():
    team = TeamFactory.create()
    form = ParticipantForm(data={}, team=team)
    assert not form.is_valid()
    assert "identifier" in form.errors
    assert "platform" in form.errors


@pytest.mark.django_db()
def test_participant_form_platform_choices_include_web_and_api():
    team = TeamFactory.create()
    form = ParticipantForm(team=team)
    values = [value for value, _label in form.fields["platform"].choices]
    assert ChannelPlatform.WEB in values
    assert ChannelPlatform.API in values


@pytest.mark.django_db()
def test_participant_form_rejects_duplicate_with_link():
    team = TeamFactory.create()
    existing = ParticipantFactory.create(
        team=team, platform=ChannelPlatform.WEB, identifier="user@example.com"
    )
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.WEB},
        team=team,
    )
    assert not form.is_valid()
    error_html = str(form.non_field_errors())
    assert existing.get_absolute_url() in error_html


@pytest.mark.django_db()
def test_participant_form_allows_same_identifier_on_different_platform():
    team = TeamFactory.create()
    ParticipantFactory.create(
        team=team, platform=ChannelPlatform.WEB, identifier="user@example.com"
    )
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.TELEGRAM},
        team=team,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_participant_form_allows_same_identifier_on_different_team():
    team_a = TeamFactory.create()
    team_b = TeamFactory.create()
    ParticipantFactory.create(
        team=team_a, platform=ChannelPlatform.WEB, identifier="user@example.com"
    )
    form = ParticipantForm(
        data={"identifier": "user@example.com", "platform": ChannelPlatform.WEB},
        team=team_b,
    )
    assert form.is_valid(), form.errors
    # Sanity: no row exists yet for team_b
    assert not Participant.objects.filter(team=team_b).exists()
```

- [ ] **Step 1.2: Run the tests to confirm they fail**

Run: `uv run pytest apps/participants/tests/test_forms.py -v`

Expected: failures because the current `ParticipantForm` has `disabled=True` on `identifier` and doesn't accept a `team` kwarg.

- [ ] **Step 1.3: Rewrite `ParticipantForm`**

Replace lines 1–19 of `apps/participants/forms.py` with this. Keep the rest of the file (`ParticipantImportForm`, `ParticipantExportForm`, `TriggerBotForm`) untouched.

```python
import csv
import io
import logging

from django import forms
from django.urls import reverse
from django.utils.html import format_html

from apps.channels.models import ChannelPlatform
from apps.experiments.models import Experiment, Participant
from apps.utils.json import PrettyJSONEncoder

logger = logging.getLogger("ocs.participants")


class ParticipantForm(forms.ModelForm):
    platform = forms.ChoiceField()

    class Meta:
        model = Participant
        fields = ("identifier", "platform", "name")

    def __init__(self, *args, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.fields["platform"].choices = self._platform_choices(team)

    @staticmethod
    def _platform_choices(team):
        platforms = list(ChannelPlatform.for_dropdown(used_platforms=[], team=team).keys()) if team else []
        platforms.extend([ChannelPlatform.WEB, ChannelPlatform.API])
        platforms.sort(key=lambda p: p.value)
        return [(p.value, p.label) for p in platforms]

    def clean(self):
        cleaned = super().clean()
        identifier = cleaned.get("identifier")
        platform = cleaned.get("platform")
        if self.team and identifier and platform:
            existing = Participant.objects.filter(
                team=self.team, platform=platform, identifier=identifier
            ).first()
            if existing:
                raise forms.ValidationError(
                    format_html(
                        'A participant with identifier "{}" already exists on this platform: '
                        '<a class="link" href="{}">view existing participant</a>',
                        identifier,
                        existing.get_absolute_url(),
                    )
                )
        return cleaned
```

Why `team` may be `None`: the broken existing call sites are gone after Task 2, but defensively returning a minimal choice list (just `WEB` + `API`) keeps the form usable in unit-test contexts that don't pass a team.

- [ ] **Step 1.4: Run the tests to confirm they pass**

Run: `uv run pytest apps/participants/tests/test_forms.py -v`

Expected: 7 passed.

- [ ] **Step 1.5: Lint and format the changed file**

Run:

```bash
uv run ruff check apps/participants/forms.py apps/participants/tests/test_forms.py --fix
uv run ruff format apps/participants/forms.py apps/participants/tests/test_forms.py
```

Expected: no remaining errors.

- [ ] **Step 1.6: Commit**

```bash
git add apps/participants/forms.py apps/participants/tests/test_forms.py
git commit -m "feat(participants): rewrite ParticipantForm with uniqueness validation"
```

---

## Task 2: Wire up CreateParticipant view (TDD)

**Files:**
- Modify: `apps/participants/views.py:110-127` (`CreateParticipant`)
- Modify: `apps/participants/urls.py:42` (flip `new=False` → `new=True`)
- Modify: `apps/participants/tests/test_views.py` (append new tests)

### Background

`CreateParticipant` exists but has two bugs:

1. It sets `form.instance.created_by = self.request.user`, but `Participant` has no `created_by` field — this would raise `AttributeError`.
2. It doesn't pass `team` into the form, so the new `ParticipantForm` from Task 1 wouldn't have one.

Also, the URL isn't registered: `apps/participants/urls.py:42` calls `make_crud_urls(..., new=False)`. After flipping that flag, the URL `participants/new/` becomes available with name `participants:participant_new`.

After success, the user should land on the new participant's detail page. The route is `participants:single-participant-home` with kwargs `team_slug` and `participant_id`.

The login pattern in this test file is `client.login(username=user.username, password="password")` — `UserFactory` sets `password="password"` for all generated users, and the first member of `TeamWithUsersFactory` is the team admin (has `experiments.add_participant`).

- [ ] **Step 2.1: Write failing tests for the create view**

Append to `apps/participants/tests/test_views.py`:

```python
@pytest.mark.django_db()
def test_create_participant_get(client, team_with_users):
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})

    response = client.get(url)

    assert response.status_code == 200
    assert b"Create Participant" in response.content


@pytest.mark.django_db()
def test_create_participant_post_success_redirects_to_detail(client, team_with_users):
    from apps.experiments.models import Participant

    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})

    response = client.post(
        url,
        {"identifier": "alice@example.com", "platform": ChannelPlatform.WEB, "name": "Alice"},
    )

    participant = Participant.objects.get(team=team_with_users, identifier="alice@example.com")
    assert participant.platform == ChannelPlatform.WEB
    assert participant.name == "Alice"
    assert response.status_code == 302
    assert response["Location"] == reverse(
        "participants:single-participant-home",
        kwargs={"team_slug": team_with_users.slug, "participant_id": participant.id},
    )


@pytest.mark.django_db()
def test_create_participant_duplicate_shows_error_with_link(client, team_with_users):
    from apps.experiments.models import Participant

    existing = ParticipantFactory.create(
        team=team_with_users, platform=ChannelPlatform.WEB, identifier="alice@example.com"
    )
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})

    response = client.post(
        url,
        {"identifier": "alice@example.com", "platform": ChannelPlatform.WEB, "name": "Alice"},
    )

    assert response.status_code == 200
    assert existing.get_absolute_url().encode() in response.content
    assert Participant.objects.filter(team=team_with_users, identifier="alice@example.com").count() == 1


@pytest.mark.django_db()
def test_create_participant_missing_fields_shows_field_errors(client, team_with_users):
    from apps.experiments.models import Participant

    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})

    response = client.post(url, {"identifier": "", "platform": "", "name": ""})

    assert response.status_code == 200
    assert not Participant.objects.filter(team=team_with_users).exists()


@pytest.mark.django_db()
def test_participant_home_shows_create_action(client, team_with_users):
    user = team_with_users.members.first()
    client.login(username=user.username, password="password")
    url = reverse("participants:participant_home", kwargs={"team_slug": team_with_users.slug})

    response = client.get(url)

    assert response.status_code == 200
    create_url = reverse("participants:participant_new", kwargs={"team_slug": team_with_users.slug})
    assert create_url.encode() in response.content
```

- [ ] **Step 2.2: Run the new tests to confirm they fail**

Run: `uv run pytest apps/participants/tests/test_views.py -v -k "create_participant or participant_home_shows_create"`

Expected: failures — the URL `participants:participant_new` doesn't resolve yet.

- [ ] **Step 2.3: Wire up the URL**

Edit `apps/participants/urls.py:42`. Change:

```python
urlpatterns.extend(make_crud_urls(views, "Participant", "participant", edit=False, delete=False, new=False))
```

to:

```python
urlpatterns.extend(make_crud_urls(views, "Participant", "participant", edit=False, delete=False))
```

(`new` defaults to `True` — see `apps/generics/urls.py:4`.)

- [ ] **Step 2.4: Fix `CreateParticipant`**

Replace the `CreateParticipant` class at `apps/participants/views.py:110-127` with:

```python
class CreateParticipant(LoginAndTeamRequiredMixin, PermissionRequiredMixin, CreateView):
    permission_required = "experiments.add_participant"
    model = Participant
    form_class = ParticipantForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Participant",
        "button_text": "Create",
        "active_tab": "participants",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team
        return kwargs

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)

    def get_success_url(self):
        return reverse(
            "participants:single-participant-home",
            kwargs={"team_slug": self.request.team.slug, "participant_id": self.object.id},
        )
```

The `created_by` line is dropped because `Participant` has no such field.

- [ ] **Step 2.5: Add the Create action on the home page**

Edit `apps/participants/views.py` inside `ParticipantHome.get_context_data` (the `actions` list, currently lines ~86–105). Add a new action **before** the existing Import action so the order reads Create → Import → Export:

```python
"actions": [
    actions.Action(
        "participants:participant_new",
        label="Create",
        icon_class="fa-solid fa-plus",
        title="Create participant",
        required_permissions=["experiments.add_participant"],
    ),
    actions.Action(
        "participants:import",
        label="Import",
        icon_class="fa-solid fa-file-import",
        title="Import participants",
        required_permissions=IMPORT_PERMISSIONS,
    ),
    actions.ModalAction(
        "participants:export",
        label="Export",
        icon_class="fa-solid fa-download",
        required_permissions=["experiments.view_participant", "experiments.view_participantdata"],
        modal_template="participants/components/export_modal.html",
        modal_context={
            "form": ParticipantExportForm(team=self.request.team),
            "modal_title": "Export Participant Data",
        },
    ),
],
```

- [ ] **Step 2.6: Run the new tests to confirm they pass**

Run: `uv run pytest apps/participants/tests/test_views.py -v -k "create_participant or participant_home_shows_create"`

Expected: 5 passed.

- [ ] **Step 2.7: Run the full participants test module to catch regressions**

Run: `uv run pytest apps/participants/tests/ -v`

Expected: all green.

- [ ] **Step 2.8: Lint and typecheck**

Run:

```bash
uv run ruff check apps/participants/views.py apps/participants/urls.py apps/participants/tests/test_views.py --fix
uv run ruff format apps/participants/views.py apps/participants/urls.py apps/participants/tests/test_views.py
uv run ty check apps/participants/
```

Expected: no errors.

- [ ] **Step 2.9: Commit**

```bash
git add apps/participants/views.py apps/participants/urls.py apps/participants/tests/test_views.py
git commit -m "feat(participants): add Create Participant view and home action"
```

---

## Task 3: Manual verification

**Files:** none modified.

This task confirms the feature works end-to-end in a real browser.

- [ ] **Step 3.1: Start services and dev server**

```bash
uv run inv up
uv run python manage.py migrate
uv run inv runserver
```

In a separate terminal:

```bash
npm run dev
```

- [ ] **Step 3.2: Exercise the happy path in a browser**

1. Log in to a team.
2. Navigate to `/a/<team-slug>/participants/`.
3. Confirm the "Create" button appears next to "Import" and "Export".
4. Click "Create". Confirm the form shows `identifier`, `platform`, `name` fields and a Create button.
5. Submit a brand-new (`identifier`, `platform`) pair. Confirm you land on the new participant's detail page.

- [ ] **Step 3.3: Exercise the duplicate path**

1. Click "Create" again from the home page.
2. Submit the same `(identifier, platform)` you just created.
3. Confirm the form re-renders with a non-field error containing a clickable link to the existing participant. Click the link and confirm it navigates to the existing record.

- [ ] **Step 3.4: Exercise the validation path**

1. Click "Create" again.
2. Submit with an empty identifier. Confirm Django's standard "This field is required" error renders next to the field.

If all four checks pass, the feature is complete.

---

## Self-Review

**Spec coverage:**
- ✅ Form fields `identifier`, `platform`, `name` — Task 1.
- ✅ Platform list matches CSV importer (`for_dropdown` keys + `WEB` + `API`, sorted by value) — Task 1.3.
- ✅ Create action on home — Task 2.5.
- ✅ Duplicate handling with link — Task 1.3 (`clean`) + Task 2.1 (test asserts link in response).
- ✅ Redirect to single-participant home — Task 2.4 (`get_success_url`).
- ✅ Permission `experiments.add_participant` — preserved on the view, plus on the home action.
- ✅ Tests for GET, POST success, duplicate, field errors, home action visibility — Task 2.1.

**Placeholder scan:** No "TBD" / "TODO" / "appropriate error handling" / "similar to" entries.

**Type consistency:** Form constructor `team=None` matches the `team=team_with_users` kwarg used in tests; `get_form_kwargs` passes `team=self.request.team`; `get_absolute_url()` is the same method called from both form-clean and the view test assertion.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-05-participant-create-view.md`.
