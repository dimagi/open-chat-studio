# Remove TypeSelectForm pattern

Tracks GitHub issue [#3286](https://github.com/dimagi/open-chat-studio/issues/3286).

## Goal

Replace the `TypeSelectForm` abstraction (`apps/generics/type_select_form.py`)
with simpler, type-specific flows. The current pattern renders all secondary
forms on the page and relies on Alpine.js (`x-show`, `x-bind:disabled`,
`x-bind:required`) to fake the active form. After this change:

- Service providers: type is chosen *before* the form via a dropdown, and the
  create view renders a single static form for that type.
- Events / Event actions: the action-type field swaps in a single secondary
  form fragment via HTMX, instead of all forms being present at once.

When done, `TypeSelectForm`, `BaseTypeSelectFormView`,
`templates/generic/type_select_form.html`, and `apps/generics/exceptions.py`
are deleted.

## Current state

### `apps/generics/type_select_form.py`

`TypeSelectForm` bundles a primary `ModelForm` and a dict of secondary forms
keyed by the value of one field on the primary form (`secondary_key_field`).
On instantiation it injects `x-model="type"` on the type select and
`x-bind:required` / `x-bind:disabled` on every field of every secondary form
so only the active form's fields submit.

### Service providers

- `apps/service_providers/utils.py:get_service_provider_config_form` builds
  the `TypeSelectForm`.
- `apps/service_providers/views.py:CreateServiceProvider` extends
  `BaseTypeSelectFormView`. URLs:
  - `<provider_type>/create/` (single create URL for any subtype)
  - `<provider_type>/<int:pk>/` (edit)
- `templates/generic/type_select_form.html` renders the primary form, then
  loops over secondary forms inside `x-show` divs.
- `templates/service_providers/llm_provider_form.html` extends that template
  via `{% block pre_secondary_form %}` / `{% block post_form %}` to add the
  default/custom/embedding model panels and the new-custom-model dialog.
- Subtype filtering: `flag_open_ai_voice_engine` (Waffle) hides
  `VoiceProviderType.openai_voice_engine`; `settings.SLACK_ENABLED` hides
  `MessagingProviderType.slack`.

### Events / Event actions

- `apps/events/forms.py:EventActionTypeSelectForm` subclasses `TypeSelectForm`
  and overrides `save()` to write `cleaned_data` from the active secondary
  form into `EventAction.params` (a JSONField).
- `get_action_params_form()` builds the form with five secondaries: `log`
  (EmptyForm), `send_message_to_bot`, `end_conversation` (EmptyForm),
  `schedule_trigger`, `pipeline_start`.
- `apps/events/views.py:_create_event_view` / `_edit_event_view` pair the
  action form with a trigger form (`StaticTriggerForm` or
  `TimeoutTriggerForm`) and render `templates/events/manage_event.html`.
- `manage_event.html` loops the action form's secondaries inside `x-show`
  divs and contains an Alpine block that disables the
  `send_message_to_bot` / `schedule_trigger` action options when the static
  trigger type is `new_bot_message`.

## Service providers — design

### URL routing

```python
# apps/service_providers/urls.py
path("<slug:provider_type>/create/<str:subtype>/", views.CreateServiceProvider.as_view(), name="new"),
path("<slug:provider_type>/<int:pk>/", views.EditServiceProvider.as_view(), name="edit"),
```

The `service_providers:new` reverse signature gains a `subtype` argument.
`service_providers:edit` is unchanged.

The LLM extra context (default/custom/embedding model panels, new-model
form) is shared between create and edit, so both views use the same template
and context-building logic for the LLM provider type.

### View

A single `CreateOrEditServiceProvider` view (kept as one class because
create and edit share most of the work and the existing view is also a
single class) replaces `BaseTypeSelectFormView`. Responsibilities:

1. Resolve `provider_type` from the URL slug (existing
   `ServiceProviderMixin`).
2. Resolve `subtype`:
   - Create: from URL kwarg. 404 if it's not a valid subtype, or if it's
     filtered out by the same flags that filter the dropdown menu (see
     "Subtype filtering" below).
   - Edit: from `instance.type`. Filtering is **not** applied on edit —
     existing providers of an excluded subtype must remain editable
     (e.g. an existing Slack messaging provider should still be editable
     after `SLACK_ENABLED` is turned off).
3. Build:
   - `primary_form` = `ModelForm` for the provider model with
     `provider.primary_fields` (e.g. `["name", "type"]`). The `type` field
     is rendered as a hidden input pre-filled with `subtype` (and
     `disabled` on edit, matching the current behavior).
   - `config_form` = `subtype.form_cls(team=..., data=..., initial=...)`.
4. Validate both. On success, save the model instance, then call
   `config_form.save(instance)` (existing protocol).
5. The LLM template still gets the model panels in context.

The current `form_valid` extras (file formset, voice post-save warnings,
team assignment) all carry over.

### Template

Replace `templates/generic/type_select_form.html` with a new template
`templates/service_providers/provider_form.html` (one form, no Alpine
type-switching). The LLM template extends that one for its extra panels and
new-custom-model dialog. The `x-data="{ type: '...' }"` wrapper goes away;
the new-model dialog hidden `<input name="type">` is set to the static
subtype value rather than `x-model="type"`.

### "Add new" → dropdown menu

`templates/generic/object_home_content.html` currently renders a single
"Add new" anchor when `new_object_url` is provided. Add an optional
`new_object_choices` context variable: a list of `(label, url)` tuples.
When provided, render a daisyUI dropdown menu instead of the plain link;
otherwise behavior is unchanged so other call sites keep working.

`templates/service_providers/service_provider_home.html` builds the
choices list — one entry per subtype, using
`reverse("service_providers:new", args=[team.slug, provider_type, subtype])`
— and passes it as `new_object_choices`. This is also the place where
flag-based filtering is applied.

### Subtype filtering

Move the existing exclusion logic out of `CreateServiceProvider.get_form`
into a single helper, e.g.
`utils.get_available_subtypes(provider, request) -> list[Enum]`, used by:

- `service_provider_home.html`'s context (which subtypes appear in the
  dropdown).
- The create view (404 if the URL kwarg subtype is not in the available
  list).

Filters today:

- `not flag_is_active(request, "flag_open_ai_voice_engine")` →
  exclude `VoiceProviderType.openai_voice_engine`.
- `not settings.SLACK_ENABLED` → exclude `MessagingProviderType.slack`.

### Backwards compatibility

- `service_providers:new` reverses now require a `subtype` arg. Update all
  call sites:
  - `apps/slack/slack_app.py:144`
    (`reverse("service_providers:new", args=[team.slug, MESSAGING])`).
  - `templates/experiments/components/prompt_builder_toolbox.html` link to
    `service_providers:new ... "llm"`.
  - Any existing tests
    (`apps/service_providers/tests/test_intron.py:89`,
    `test_views.py:62`).
- Old bookmarks pointing at `<provider_type>/create/` (no subtype) will
  404. This is acceptable for an admin-only page; no redirect needed.

## Events / Event actions — design

### URL routing

Add one new endpoint:

```python
# apps/events/urls.py
path(
    "experiments/<int:experiment_id>/events/action-params/",
    views.action_params_form_view,
    name="action_params_form",
),
```

Returns the rendered HTML for the secondary form matching `action_type` (a
GET query param). This is an HTMX fragment — no surrounding layout.

### Form refactor

Delete `EventActionTypeSelectForm`. Move its `save()` semantics (write
`cleaned_data` into `EventAction.params`) into either `EventActionForm`
itself, or the view. Recommendation: keep it in the view, since the
secondary form is a separate object and the view already orchestrates
multiple forms.

`get_action_params_form` is replaced by two helpers:

- `get_action_secondary_form_class(action_type) -> type[forms.Form]`
- `build_action_secondary_form(action_type, *, data=None, initial=None,
  team_id, experiment_id) -> forms.Form`

A small registry dict keeps the mapping in one place:

```python
ACTION_PARAMS_FORMS = {
    "log": EmptyForm,
    "send_message_to_bot": SendMessageToBotForm,
    "end_conversation": EmptyForm,
    "schedule_trigger": ScheduledMessageConfigForm,
    "pipeline_start": PipelineStartForm,
}
```

### View refactor

`_create_event_view` and `_edit_event_view` build three forms:

1. `trigger_form` (unchanged).
2. `action_primary_form` = `EventActionForm(...)`.
3. `action_params_form` = `build_action_secondary_form(action_type, ...)`
   where `action_type` is taken from `request.POST["action_type"]` (POST)
   or from `instance.action_type` / the first valid choice (GET).

On POST: validate all three; on success, save the trigger and the action
(with `params = action_params_form.cleaned_data`). On the
`new_bot_message` × `send_message_to_bot|schedule_trigger` invalid combo,
keep the existing validation in `EventActionForm.clean`.

### Template refactor

`templates/events/manage_event.html`:

- Drop the `{% for key, form in action_form.secondary.items %}` loop and
  the surrounding `x-show` divs.
- Add `<div id="action-params">` containing the *current* action's
  secondary form (rendered server-side on initial load).
- The `action_type` `<select>` gets HTMX:
  ```html
  hx-get="{% url 'events:action_params_form' experiment.id %}"
  hx-target="#action-params"
  hx-swap="innerHTML"
  hx-trigger="change"
  hx-include="[name=action_type]"
  ```
- Trigger-type filtering of action options: keep the existing Alpine block
  that disables `send_message_to_bot` / `schedule_trigger` options when
  the static trigger type is `new_bot_message`. It only manipulates the
  options of the action `<select>`, which remains. After the action
  options change, fire a `change` event on the select so HTMX reloads the
  params form.

### Fragment endpoint

```python
# apps/events/views.py
@login_and_team_required
def action_params_form_view(request, team_slug: str, experiment_id: str):
    # No extra permission — this only renders form HTML and is reachable
    # from the same pages that already require event create/change perms.
    action_type = request.GET.get("action_type")
    if action_type not in ACTION_PARAMS_FORMS:
        return HttpResponseBadRequest()
    form = build_action_secondary_form(
        action_type,
        team_id=request.team.id,
        experiment_id=experiment_id,
    )
    return render(request, "events/_action_params_form.html", {"form": form})
```

`_action_params_form.html` is a tiny partial — just
`{% render_form_fields form %}` plus non-field errors.

## Cleanup checklist

After both refactors are in place, remove:

- `apps/generics/type_select_form.py`
- `apps/generics/exceptions.py` (`TypeSelectFormError` is the only thing in
  it; check no other modules import from it).
- `BaseTypeSelectFormView` in `apps/generics/views.py` (and unused imports
  it pulls in).
- `templates/generic/type_select_form.html`
- All `x-bind:disabled` / `x-bind:required` / `x-model="type"` injection
  is gone with the dataclass.

## Tests

- Update `apps/service_providers/tests/test_views.py` — `service_providers:new`
  now needs a `subtype` argument.
- Update `apps/service_providers/tests/test_intron.py:89` similarly.
- Add a test that the create-URL 404s for a flag-excluded subtype
  (`flag_open_ai_voice_engine` off, hitting the openai-voice-engine URL).
- Add a test for `action_params_form_view` covering each `action_type`.
- Existing event-creation tests should keep passing as long as the form
  layout stays equivalent; expect to update template assertions that look
  for hidden secondary forms.

## Out of scope

- No changes to the underlying provider/action models or stored data.
- No changes to subtype filtering rules (the existing two filters carry
  over verbatim).
- No changes to permissions.
