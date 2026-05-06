# Participant Create View — Design

## Background

The participants app currently provides only two ways to add participants to a team:

1. **CSV import** (`participants:import`) — bulk upload via `ParticipantImportForm` and `process_participant_import`.
2. **Implicit creation** during chat sessions — participants are created automatically when a session starts with an unknown identifier.

There is no UI to create a single participant by hand. A `CreateParticipant` view exists at `apps/participants/views.py:110` but is unreachable and broken:

- `apps/participants/urls.py:42` calls `make_crud_urls(..., new=False)`, so no URL is registered.
- `ParticipantForm` (`apps/participants/forms.py:13`) disables `identifier` and `public_id` and only exposes `user` — useless for creation.
- `CreateParticipant.form_valid` sets `form.instance.created_by`, but `Participant` has no `created_by` field. This would raise `AttributeError` if the view were ever reached.

This spec adds a working "Create Participant" form, accessible from the participants home page.

## Goals

- Allow users with `experiments.add_participant` to create a single participant via a form.
- Present a clear error (with a link to the existing record) when the user tries to create a duplicate.
- Land the user on the new participant's detail page so they can immediately edit data, etc.

## Non-goals

- Setting experiment-scoped `ParticipantData` (`data.*`) at create time. That stays in CSV import and the per-participant data edit flow.
- Exposing `remote_id` or `user` fields on the form.
- Bulk creation — CSV import remains the path for that.

## Decisions

| Question | Decision |
|---|---|
| Form fields | `identifier`, `platform`, `name` (name optional) |
| Platform dropdown | Same set the CSV importer accepts: `ChannelPlatform.for_dropdown(used_platforms=[], team=team)` keys, plus `WEB` and `API`, sorted by value |
| Entry point | "Create" action button on `ParticipantHome`, alongside Import / Export |
| Duplicate handling | Reject with a form-level error that links to the existing participant |
| Post-success redirect | New participant's detail page (`participants:single-participant-home`) |
| Permission | `experiments.add_participant` |

## Components

### 1. Form — `apps/participants/forms.py`

Replace `ParticipantForm` with a working `ModelForm`:

```python
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
        platforms = list(ChannelPlatform.for_dropdown(used_platforms=[], team=team).keys())
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
                link = format_html(
                    '<a class="link" href="{}">existing participant</a>',
                    existing.get_absolute_url(),
                )
                raise forms.ValidationError(
                    format_html(
                        "A participant with identifier {} already exists for this platform: {}",
                        identifier, link,
                    )
                )
        return cleaned
```

`identifier` keeps its model-level `max_length=320`; `name` keeps `blank=True`.

### 2. View — `apps/participants/views.py`

Fix `CreateParticipant`:

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
            args=[self.request.team.slug, self.object.id],
        )
```

The broken `form.instance.created_by` line is dropped.

### 3. URL — `apps/participants/urls.py`

Change `make_crud_urls(views, "Participant", "participant", edit=False, delete=False, new=False)` to `new=True`. This registers `participants/new/` with name `participants:participant_new`, pointing at `CreateParticipant.as_view()`.

### 4. Home action — `ParticipantHome.get_context_data`

Add a third action ahead of Import / Export so it reads naturally left-to-right:

```python
actions.Action(
    "participants:participant_new",
    label="Create",
    icon_class="fa-solid fa-plus",
    title="Create participant",
    required_permissions=["experiments.add_participant"],
),
```

### 5. Template

Reuse `generic/object_form.html` — no new template needed. Form-level errors render via the standard `form.non_field_errors` block, which renders the `format_html` link safely.

## Data flow

```
GET  /a/<team>/participants/new/  → renders ParticipantForm
POST /a/<team>/participants/new/  → form.clean() runs uniqueness check
                                    on duplicate: 200 with non-field error + link to existing
                                    on success:  Participant.objects.create(...)
                                                 302 → /a/<team>/participants/<new-id>
```

## Error handling

- **Duplicate `(team, platform, identifier)`** — caught in `clean()`, surfaced as a non-field error containing an HTML link to the existing participant.
- **Empty identifier or invalid platform** — standard Django field validation.
- **Permission denied** — `PermissionRequiredMixin` returns 403 for users without `experiments.add_participant`.

## Testing

Add tests in `apps/participants/tests/`:

1. `GET` renders the form for a user with `add_participant`; returns 403 without it.
2. `POST` with valid data creates the participant, sets `team` correctly, and redirects to `participants:single-participant-home` for the new id.
3. `POST` with `(platform, identifier)` matching an existing participant returns 200, includes the existing participant's URL in the response, and does **not** create a duplicate row.
4. `POST` with empty `identifier` or missing `platform` returns 200 with field errors and creates nothing.
5. The participant home page renders the new "Create" action for users with permission.

## Files changed

- `apps/participants/forms.py` — rewrite `ParticipantForm`.
- `apps/participants/views.py` — fix `CreateParticipant.form_valid`, add `get_form_kwargs`, override `get_success_url`, add Create action in `ParticipantHome`.
- `apps/participants/urls.py` — flip `new=False` → `new=True`.
- `apps/participants/tests/` — new test cases for the create flow.
