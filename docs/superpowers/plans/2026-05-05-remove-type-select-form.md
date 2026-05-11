# Remove TypeSelectForm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `TypeSelectForm` abstraction with simpler, type-specific
flows: dropdown-then-form for service providers, HTMX fragment for event
actions. Delete the dataclass, base view, generic template, and Alpine
attribute injection when done.

**Architecture:** Service provider create URLs gain a `<subtype>` segment;
the view renders one ModelForm + one config form. Events gain an HTMX
endpoint that returns just the action-params secondary form for a given
`action_type`. The `EventActionTypeSelectForm` is replaced by an in-view
orchestration of three plain forms.

**Tech Stack:** Django (forms, views, URL routing), HTMX, Alpine.js
(remaining only for the trigger-type / action-type filtering), daisyUI.

**Spec:** `docs/superpowers/specs/2026-05-05-remove-type-select-form-design.md`

---

## File Structure

### Service providers

| Path | Action | Responsibility |
|------|--------|----------------|
| `apps/service_providers/utils.py` | Modify | Add `get_available_subtypes(provider, request)` helper. Replace `get_service_provider_config_form` with `get_service_provider_forms` returning `(primary_form, config_form)`. |
| `apps/service_providers/views.py` | Modify | `CreateServiceProvider` accepts a `subtype` URL kwarg on create, derives it from the instance on edit. Renders a single config form. Drops `BaseTypeSelectFormView` inheritance. |
| `apps/service_providers/urls.py` | Modify | Add `<str:subtype>` to the `create` URL. Remove the legacy `llm/create/` path (LLM uses the same routing as other providers; the LLM-specific view stays for the extra context). |
| `templates/service_providers/provider_form.html` | Create | New base template for service provider create/edit. One form, no Alpine type-switching. |
| `templates/service_providers/llm_provider_form.html` | Modify | Extend new base. Replace `x-model="type"` hidden input with a static value. |
| `templates/service_providers/service_provider_home.html` | Modify | Build `new_object_choices` (one per available subtype) and pass to the home content include. |
| `templates/generic/object_home_content.html` | Modify | When `new_object_choices` provided, render daisyUI dropdown menu in place of the single anchor. |
| `apps/service_providers/tests/test_views.py` | Modify | Update `service_providers:new` reverses to include `subtype`. |
| `apps/service_providers/tests/test_intron.py` | Modify | Same. |
| `apps/slack/slack_app.py` | Modify | Update `service_providers:new` reverse at line 144. |
| `templates/experiments/components/prompt_builder_toolbox.html` | Modify | Update `service_providers:new` URL tag at line 80. |

### Events / Event actions

| Path | Action | Responsibility |
|------|--------|----------------|
| `apps/events/forms.py` | Modify | Add `ACTION_PARAMS_FORMS` registry. Add `build_action_params_form(action_type, ...)`. Delete `EventActionTypeSelectForm`. Delete `get_action_params_form`. |
| `apps/events/views.py` | Modify | Refactor `_create_event_view` / `_edit_event_view` to compose three plain forms. Add `action_params_form_view` fragment endpoint. |
| `apps/events/urls.py` | Modify | Add `action-params/` route. |
| `templates/events/manage_event.html` | Modify | Drop the for-loop over secondary forms. Add HTMX-loaded `#action-params` div. Adjust the Alpine block to dispatch `change` on the select after rewriting its value. |
| `templates/events/_action_params_form.html` | Create | Tiny partial: the active secondary form's fields and non-field errors. |

### Cleanup (last)

| Path | Action |
|------|--------|
| `apps/generics/type_select_form.py` | Delete |
| `apps/generics/exceptions.py` | Delete |
| `apps/generics/views.py` | Modify — drop `BaseTypeSelectFormView` and unused imports |
| `templates/generic/type_select_form.html` | Delete |

---

## Task 1: Add `get_available_subtypes` helper

**Files:**
- Modify: `apps/service_providers/utils.py`

- [ ] **Step 1: Add helper function**

In `apps/service_providers/utils.py`, after the imports and before
`get_service_provider_config_form` (currently around line 73), add:

```python
def get_available_subtypes(provider: ServiceProvider, request) -> list:
    """Return the subtypes for ``provider`` available to the given request.

    Filters out subtypes gated by feature flags / settings.
    """
    excluded = set()
    if provider == ServiceProvider.voice and not flag_is_active(request, "flag_open_ai_voice_engine"):
        excluded.add(VoiceProviderType.openai_voice_engine)
    if provider == ServiceProvider.messaging and not settings.SLACK_ENABLED:
        excluded.add(MessagingProviderType.slack)
    return [subtype for subtype in provider.subtype if subtype not in excluded]
```

Add the imports at module top (not inside the function):

```python
from django.conf import settings
from waffle import flag_is_active
```

- [ ] **Step 2: Run lint**

```bash
uv run ruff check apps/service_providers/utils.py --fix
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add apps/service_providers/utils.py
git commit -m "refactor: add get_available_subtypes helper for service providers"
```

---

## Task 2: Replace form factory with `get_service_provider_forms`

**Files:**
- Modify: `apps/service_providers/utils.py`

- [ ] **Step 1: Replace the factory**

In `apps/service_providers/utils.py`, replace `get_service_provider_config_form`
with two functions. Delete the old `get_service_provider_config_form` and the
`TypeSelectForm` import; add:

```python
def get_service_provider_forms(
    provider: ServiceProvider,
    team,
    subtype,
    *,
    data=None,
    instance=None,
):
    """Return ``(primary_form, config_form)`` for a service provider create/edit.

    ``subtype`` is the enum member identifying which config form to build.
    On edit, the subtype is derived from the instance by the caller.
    """
    initial_config = provider.get_form_initial(instance) if instance else None
    primary_form = _get_main_form(provider, instance=instance, data=data, fixed_subtype=subtype)
    config_form = subtype.form_cls(
        team=team,
        data=data.copy() if data else None,
        initial=initial_config,
    )
    return primary_form, config_form
```

Update `_get_main_form` to accept the fixed subtype (the field becomes a
hidden input pre-filled with the subtype value):

```python
def _get_main_form(provider: ServiceProvider, *, instance=None, data=None, fixed_subtype):
    """Get the main 'model form' for the service provider.

    The provider-type field is rendered as a hidden input with the chosen
    subtype as its initial value. On edit, it is also disabled, matching
    the previous behavior.
    """
    form_cls = forms.modelform_factory(
        provider.model,
        fields=provider.primary_fields,
        formfield_callback=functools.partial(formfield_for_dbfield, provider=provider),
    )
    initial = {provider.provider_type_field: str(fixed_subtype)}
    form = form_cls(data=data, instance=instance, initial=initial)
    type_field = form.fields[provider.provider_type_field]
    type_field.widget = forms.HiddenInput()
    if instance:
        type_field.disabled = True
    return form
```

Remove the `TypeSelectForm` import at the top of the file.

- [ ] **Step 2: Run lint**

```bash
uv run ruff check apps/service_providers/utils.py --fix
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add apps/service_providers/utils.py
git commit -m "refactor: replace TypeSelectForm-based factory with plain form pair"
```

---

## Task 3: Add `<subtype>` to service provider create URL

**Files:**
- Modify: `apps/service_providers/urls.py`

- [ ] **Step 1: Update URL patterns**

Replace the `new` and `edit` patterns in `apps/service_providers/urls.py`.
Drop the LLM-specific `llm/create/` and `llm/<int:pk>/` patterns; the LLM
view will be wired through the generic patterns and a provider_type-based
dispatch in the view. Keep `llm_provider_model/...` paths as-is.

```python
urlpatterns = [
    path("llm_provider_model/create/", views.create_llm_provider_model, name="llm_provider_model_new"),
    path(
        "llm_provider_model/<int:pk>/delete/",
        views.delete_llm_provider_model,
        name="llm_provider_model_delete",
    ),
    path("<slug:provider_type>/table/", views.ServiceProviderTableView.as_view(), name="table"),
    path("<slug:provider_type>/create/<str:subtype>/", views.CreateServiceProvider.as_view(), name="new"),
    path("<slug:provider_type>/<int:pk>/", views.CreateServiceProvider.as_view(), name="edit"),
    path("<slug:provider_type>/<int:pk>/delete/", views.delete_service_provider, name="delete"),
    path("<slug:provider_type>/<int:pk>/remove-file/<int:file_id>", views.remove_file, name="delete_file"),
    path("<slug:provider_type>/<int:pk>/upload-file/", views.AddFileToProvider.as_view(), name="add_file"),
    path("<slug:provider_type>/<int:pk>/sync-voices/", views.sync_voices, name="sync_voices"),
]
```

- [ ] **Step 2: Commit**

```bash
git add apps/service_providers/urls.py
git commit -m "refactor: add subtype to service-provider create URL"
```

---

## Task 4: Rewrite `CreateServiceProvider` view

**Files:**
- Modify: `apps/service_providers/views.py`

- [ ] **Step 1: Rewrite the view**

In `apps/service_providers/views.py`, replace `CreateServiceProvider`
(currently lines 134–190) with a non-`BaseTypeSelectFormView` implementation.
Also delete `LlmProviderView` (its responsibilities are folded into
`CreateServiceProvider` via provider-type-aware extra context):

```python
from django import views as django_views
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render

from apps.files.forms import get_file_formset

from .utils import get_available_subtypes, get_service_provider_forms


class CreateServiceProvider(
    LoginAndTeamRequiredMixin, django_views.View, ServiceProviderMixin, PermissionRequiredMixin
):
    template_name = "service_providers/provider_form.html"

    def get_permission_required(self):
        if self.kwargs.get("pk"):
            return (self.provider_type.get_permission("change"),)
        return (self.provider_type.get_permission("add"),)

    def _resolve_subtype(self):
        instance = self._get_instance()
        if instance:
            return self.provider_type.subtype(instance.type)
        slug = self.kwargs.get("subtype")
        try:
            subtype = self.provider_type.subtype(slug)
        except ValueError as exc:
            raise Http404(f"Unknown subtype: {slug}") from exc
        if subtype not in get_available_subtypes(self.provider_type, self.request):
            raise Http404("Subtype is not enabled")
        return subtype

    def _get_instance(self):
        if not self.kwargs.get("pk"):
            return None
        return get_object_or_404(
            self.provider_type.model, team=self.request.team, pk=self.kwargs["pk"]
        )

    def get(self, request, *args, **kwargs):
        subtype = self._resolve_subtype()
        instance = self._get_instance()
        primary_form, config_form = get_service_provider_forms(
            self.provider_type, team=request.team, subtype=subtype, instance=instance
        )
        return render(request, self.template_name, self._get_context(primary_form, config_form, subtype, instance))

    def post(self, request, *args, **kwargs):
        subtype = self._resolve_subtype()
        instance = self._get_instance()
        primary_form, config_form = get_service_provider_forms(
            self.provider_type, team=request.team, subtype=subtype, data=request.POST, instance=instance
        )

        file_formset = None
        if request.FILES:
            file_formset = get_file_formset(request, formset_cls=config_form.file_formset_form)

        if primary_form.is_valid() and config_form.is_valid() and (not file_formset or file_formset.is_valid()):
            with transaction.atomic():
                obj = primary_form.save(commit=False)
                obj.team = request.team
                config_form.save(obj)
                obj.save()
                if file_formset:
                    files = file_formset.save(request)
                    obj.add_files(files)
                if isinstance(obj, VoiceProvider):
                    for warning in obj.run_post_save_hook():
                        messages.warning(request, warning)
            return HttpResponseRedirect(self.get_success_url())

        if file_formset and not file_formset.is_valid():
            messages.error(request, ", ".join(file_formset.non_form_errors()))
        return render(request, self.template_name, self._get_context(primary_form, config_form, subtype, instance))

    def _get_context(self, primary_form, config_form, subtype, instance):
        ctx = {
            "primary_form": primary_form,
            "config_form": config_form,
            "subtype": subtype,
            "object": instance,
            "title": f"Edit {instance.name}" if instance else self.provider_type.label,
            "button_text": "Update" if instance else "Create",
            "active_tab": "manage-team",
        }
        if instance and isinstance(instance, VoiceProvider) and instance.type == VoiceProviderType.elevenlabs.value:
            ctx["sync_voices_url"] = reverse(
                "service_providers:sync_voices",
                kwargs={
                    "team_slug": self.request.team.slug,
                    "provider_type": "voice",
                    "pk": instance.pk,
                },
            )
        if self.provider_type == ServiceProvider.llm:
            ctx["template_name"] = "service_providers/llm_provider_form.html"
            default_llm_models_by_type = _get_models_by_type(LlmProviderModel.objects.filter(team=None))
            embedding_models_by_type = _get_models_by_type(EmbeddingProviderModel.objects.filter(team=None))
            custom_llm_models_by_type = _get_models_by_type(LlmProviderModel.objects.filter(team=self.request.team))
            ctx.update(
                {
                    "default_llm_models_by_type": default_llm_models_by_type,
                    "custom_llm_models_by_type": custom_llm_models_by_type,
                    "embedding_models_by_type": embedding_models_by_type,
                    "new_model_form": LlmProviderModelForm(self.request.team),
                }
            )
        return ctx

    def get_success_url(self):
        return resolve_url("single_team:manage_team", team_slug=self.request.team.slug)
```

Pick whichever template the view should render: switch on `provider_type`
inside `get`/`post` so the LLM template extends the base. Replace the two
calls to `render(... self.template_name ...)` with:

```python
template = (
    "service_providers/llm_provider_form.html"
    if self.provider_type == ServiceProvider.llm
    else self.template_name
)
return render(request, template, self._get_context(...))
```

(Drop the `template_name` ctx key from `_get_context` — it was a placeholder
in the snippet above.)

Delete `LlmProviderView` and `_get_models_by_type` is referenced from
`_get_context`, so keep `_get_models_by_type` in the module.

- [ ] **Step 2: Update imports at the top of the file**

Drop `from ..generics.views import BaseTypeSelectFormView`. Add
`from django.http import Http404` if not already present. Drop the
`flag_is_active` import if unused (filtering moved to `utils.py`).

- [ ] **Step 3: Run lint**

```bash
uv run ruff check apps/service_providers/views.py --fix
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add apps/service_providers/views.py
git commit -m "refactor: rewrite CreateServiceProvider without TypeSelectForm"
```

---

## Task 5: New `provider_form.html` template

**Files:**
- Create: `templates/service_providers/provider_form.html`

- [ ] **Step 1: Create template**

Write `templates/service_providers/provider_form.html`:

```django
{# Service provider create / edit. Replaces generic/type_select_form.html. #}
{% extends "web/app/app_base.html" %}
{% load form_tags i18n %}
{% block app %}
  <div class="app-card max-w-5xl mx-auto">
    <h1 class="pg-title">{{ title }}</h1>
    {% block pre_form %}{% endblock pre_form %}
    <form method="post" class="my-2" enctype="multipart/form-data">
      {% csrf_token %}
      {% render_form_fields primary_form %}
      {% if config_form.custom_template %}
        {% include config_form.custom_template with object=object form=config_form %}
      {% else %}
        {% render_form_fields config_form %}

        {% if config_form.allow_file_upload %}
          <hr class="my-4">
          {% if not object.id %}
            {% include "files/partials/file_formset.html" %}
          {% else %}
            {% with delete_url=object.remove_file_url upload_url=object.add_file_url %}
              {% include "files/partials/file_list.html" with files=object.get_files nested=True %}
            {% endwith %}
          {% endif %}
        {% endif %}
      {% endif %}
      <input type="submit" class="btn btn-primary mt-2" value="{{ button_text }}">
    </form>
    {% block post_form %}
      {% if sync_voices_url %}
        <form method="post" action="{{ sync_voices_url }}" class="mt-4">
          {% csrf_token %}
          <button type="submit" class="btn btn-outline">Sync Voices from ElevenLabs</button>
        </form>
      {% endif %}
    {% endblock post_form %}
  </div>
{% endblock app %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/service_providers/provider_form.html
git commit -m "refactor: add provider_form.html for single-form service provider view"
```

---

## Task 6: Update LLM provider template

**Files:**
- Modify: `templates/service_providers/llm_provider_form.html`

- [ ] **Step 1: Replace template**

Overwrite `templates/service_providers/llm_provider_form.html` with:

```django
{% extends "service_providers/provider_form.html" %}
{% load form_tags i18n %}
{% block pre_form %}
  <div class="my-4 flex flex-col gap-4">
    <div>
      <h2 class="font-bold text-lg">Default LLM Models
        {% blocktranslate asvar help_text %}
          Default models are the published models for this provider. If you need to use a model that isn't in
          this list, create a custom model.
        {% endblocktranslate %}
        {% include "generic/help.html" with help_content=help_text %}
      </h2>
      {% include "service_providers/components/llm_models.html" with llm_models_by_type=default_llm_models_by_type %}
    </div>

    <div>
      <div class="flex justify-between">
        <h2 class="font-bold text-lg">Custom LLM Models
          {% blocktranslate asvar help_text %}
            Custom models are specific to this provider type and are available to all providers of the same type.
          {% endblocktranslate %}
          {% include "generic/help.html" with help_content=help_text %}
        </h2>
        <div class="tooltip" data-tip="Add a new custom model">
          <button class="btn btn-primary btn-xs" type="button" onclick="new_custom_model.showModal()">
            <i class="fa-solid fa-plus"></i>
          </button>
        </div>
      </div>
      <div id="custom_model_list">
        {% include "service_providers/components/custom_llm_models.html" with llm_models_by_type=custom_llm_models_by_type %}
      </div>
    </div>

    <div>
      <h2 class="font-bold text-lg">Embedding Models
        {% blocktranslate asvar help_text %}
          Embedding models are used to convert text into vector representations.
        {% endblocktranslate %}
        {% include "generic/help.html" with help_content=help_text %}
      </h2>
      {% include "service_providers/components/embedding_models.html" with embedding_models_by_type=embedding_models_by_type %}
    </div>
  </div>
{% endblock pre_form %}

{% block post_form %}
  {{ block.super }}
  <dialog id="new_custom_model" class="modal">
    <div class="modal-box">
      <h3 class="text-lg font-bold">Create a new Custom Model</h3>
      <div id="new_model_form">
        <input name="type" type="hidden" value="{{ subtype }}" id="id_type">
        {% render_form_fields new_model_form "name" "max_token_limit" %}
      </div>
      <div class="modal-action">
        <form method="dialog">
          <button class="btn btn-primary"
                  hx-post="{% url "service_providers:llm_provider_model_new" request.team.slug %}"
                  hx-include="#new_model_form"
                  hx-target="#custom_model_list"
                  hx-swap="innerHTML"
                  hx-on::after-request="new_custom_model.close()">
            Save
          </button>
          <button class="btn">Cancel</button>
        </form>
      </div>
    </div>
  </dialog>
{% endblock post_form %}
```

Note `value="{{ subtype }}"` replaces `x-model="type"` — the subtype is
fixed for the page so no Alpine binding is needed.

- [ ] **Step 2: Commit**

```bash
git add templates/service_providers/llm_provider_form.html
git commit -m "refactor: extend new provider_form base from llm template"
```

---

## Task 7: Add dropdown menu support to `object_home_content.html`

**Files:**
- Modify: `templates/generic/object_home_content.html`

- [ ] **Step 1: Add dropdown branch**

Find the existing "Add new" anchor block (around lines 42–46):

```django
{% if new_object_url %}
  <a data-cy="btn-new" class="btn btn-sm {{ button_style|default:"btn-primary" }}"
     href="{{ new_object_url }}">Add new
  </a>
{% endif %}
```

Replace it with:

```django
{% if new_object_choices %}
  <details class="dropdown dropdown-end" data-cy="btn-new-dropdown">
    <summary data-cy="btn-new" class="btn btn-sm {{ button_style|default:"btn-primary" }}">Add new
      <i class="fa-solid fa-caret-down ml-1"></i>
    </summary>
    <ul class="menu dropdown-content rounded-box bg-base-100 z-1 w-64 p-2 shadow-sm">
      {% for label, url in new_object_choices %}
        <li><a href="{{ url }}">{{ label }}</a></li>
      {% endfor %}
    </ul>
  </details>
{% elif new_object_url %}
  <a data-cy="btn-new" class="btn btn-sm {{ button_style|default:"btn-primary" }}"
     href="{{ new_object_url }}">Add new
  </a>
{% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/generic/object_home_content.html
git commit -m "refactor: support new_object_choices dropdown in object_home_content"
```

---

## Task 8: Wire dropdown choices into `service_provider_home.html`

**Files:**
- Modify: `templates/service_providers/service_provider_home.html`
- Modify: `apps/service_providers/views.py` — add a context-providing helper or template tag

The simplest approach: build the choices list in a template context processor
or in the template via a custom inclusion tag. Since this template is included
elsewhere with the `provider_type` slug, the cleanest path is a small template
tag that receives the request + provider_type and emits the list.

- [ ] **Step 1: Add a template tag**

Create or add to `apps/service_providers/templatetags/` a tag. If a `templatetags`
package doesn't exist for this app yet, create it:

```bash
mkdir -p apps/service_providers/templatetags
touch apps/service_providers/templatetags/__init__.py
```

Create `apps/service_providers/templatetags/service_provider_tags.py`:

```python
from django import template
from django.urls import reverse

from apps.service_providers.utils import ServiceProvider, get_available_subtypes

register = template.Library()


@register.simple_tag(takes_context=True)
def service_provider_subtype_choices(context, provider_type_slug):
    """Return a list of ``(label, url)`` tuples for available subtypes.

    Used to build the "Add new" dropdown for service provider home.
    """
    request = context["request"]
    provider = ServiceProvider[provider_type_slug]
    return [
        (
            str(subtype.label),
            reverse(
                "service_providers:new",
                kwargs={
                    "team_slug": request.team.slug,
                    "provider_type": provider.slug,
                    "subtype": str(subtype),
                },
            ),
        )
        for subtype in get_available_subtypes(provider, request)
    ]
```

- [ ] **Step 2: Use the tag in the home template**

Replace `templates/service_providers/service_provider_home.html` with:

```django
{% load i18n team_tags service_provider_tags %}
<div>
  {% has_perm "service_providers" perm as allow_new %}
  {% url "service_providers:table" request.team.slug provider_type as table_url %}
  {% service_provider_subtype_choices provider_type as new_object_choices %}
  {% with button_style="btn-outline" title_class="pg-subtitle" %}
    {% include "generic/object_home_content.html" with new_object_choices=new_object_choices table_url=table_url data_cy_title="title-"|add:provider_type %}
  {% endwith %}
</div>
```

- [ ] **Step 3: Run lint**

```bash
uv run ruff check apps/service_providers/templatetags/ --fix
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add apps/service_providers/templatetags/ templates/service_providers/service_provider_home.html
git commit -m "refactor: wire subtype dropdown into service provider home"
```

---

## Task 9: Update all `service_providers:new` reverse callers

**Files:**
- Modify: `apps/slack/slack_app.py:144`
- Modify: `templates/experiments/components/prompt_builder_toolbox.html:80`

- [ ] **Step 1: Update slack_app.py**

In `apps/slack/slack_app.py` around line 144, find:

```python
"Location": reverse("service_providers:new", args=[team.slug, MESSAGING]),
```

Change to (the slack flow always lands users on the Slack provider create page):

```python
"Location": reverse(
    "service_providers:new",
    args=[team.slug, MESSAGING, MessagingProviderType.slack.value],
),
```

Add the import at module top if missing:

```python
from apps.service_providers.models import MessagingProviderType
```

- [ ] **Step 2: Update prompt_builder_toolbox.html**

In `templates/experiments/components/prompt_builder_toolbox.html` line 80,
find:

```django
<a class="text-error link" href="{% url "service_providers:new" request.team.slug "llm" %}">LLM Provider</a>
```

Replace with a link to the LLM home page (the user picks which provider
subtype) rather than guessing:

```django
<a class="text-error link" href="{% url "single_team:manage_team" request.team.slug %}#title-llm">LLM Provider</a>
```

Verify the anchor `title-llm` exists by reading
`templates/service_providers/service_provider_home.html` — the title element
in `object_home_content.html` uses `id="{{ title|slugify }}"`. The provider
title is `LLM Service Provider`, so the slug is `llm-service-provider`. Use
that instead:

```django
<a class="text-error link" href="{% url "single_team:manage_team" request.team.slug %}#llm-service-provider">LLM Provider</a>
```

- [ ] **Step 3: Commit**

```bash
git add apps/slack/slack_app.py templates/experiments/components/prompt_builder_toolbox.html
git commit -m "refactor: update service_providers:new callers for new subtype URL"
```

---

## Task 10: Update service provider tests

**Files:**
- Modify: `apps/service_providers/tests/test_views.py`
- Modify: `apps/service_providers/tests/test_intron.py`

- [ ] **Step 1: Update `test_create_view`**

In `apps/service_providers/tests/test_views.py`, replace
`test_create_view` (lines 57–64). Each provider has multiple subtypes;
parametrize over the first available subtype per provider:

```python
@pytest.mark.parametrize("provider", list(ServiceProvider))
@pytest.mark.django_db()
def test_create_view(provider, team_with_users, authed_client):
    """Test that the create view renders without error."""
    subtype = next(iter(provider.subtype))
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={
                "team_slug": team_with_users.slug,
                "provider_type": provider.slug,
                "subtype": str(subtype),
            },
        )
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Add a 404 test for filtered subtypes**

Append to `test_views.py`:

```python
@pytest.mark.django_db()
def test_create_view_404_for_filtered_subtype(team_with_users, authed_client, settings):
    """openai_voice_engine is gated by the flag_open_ai_voice_engine flag."""
    settings.SLACK_ENABLED = True  # ensure unrelated filter is off
    response = authed_client.get(
        reverse(
            "service_providers:new",
            kwargs={
                "team_slug": team_with_users.slug,
                "provider_type": "voice",
                "subtype": VoiceProviderType.openai_voice_engine.value,
            },
        )
    )
    assert response.status_code == 404
```

- [ ] **Step 3: Update `test_intron.py`**

Find around line 88 the `reverse("service_providers:new", ...)` call. Update
the kwargs:

```python
url = reverse(
    "service_providers:new",
    kwargs={
        "team_slug": team_with_users.slug,
        "provider_type": "voice",
        "subtype": VoiceProviderType.intron.value,
    },
)
```

Drop the `"type": VoiceProviderType.intron.value,` entry from the POST data
dict — the type is now in the URL and the form receives it via initial.

Also update the docstring in this test that mentions `BaseTypeSelectFormView`
and `apps/generics/type_select_form.py`. Replace:

```python
"""Creating an intron provider via the view seeds voices end-to-end.

Form field convention: secondary forms in BaseTypeSelectFormView are instantiated without
a Django form prefix (confirmed by reading apps/service_providers/utils.py:93 and
apps/generics/type_select_form.py). Fields are submitted bare (e.g. 'intron_api_key'),
not prefixed (e.g. 'intron-intron_api_key').
"""
```

with:

```python
"""Creating an intron provider via the view seeds voices end-to-end.

Form field convention: the config form is built without a Django form prefix
(see apps/service_providers/utils.py). Fields are submitted bare
(e.g. 'intron_api_key'), not prefixed (e.g. 'intron-intron_api_key').
"""
```

- [ ] **Step 4: Run service provider tests**

```bash
uv run pytest apps/service_providers/tests/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/service_providers/tests/
git commit -m "test: update service provider tests for new subtype URL"
```

---

## Task 11: Smoke-test service providers end to end

- [ ] **Step 1: Run a wider test sweep**

```bash
uv run pytest apps/service_providers/ apps/slack/ apps/experiments/tests/ -v
```

Expected: all pass.

- [ ] **Step 2: If tests fail**

Read the failure, identify whether it's a missed `service_providers:new`
caller or a missed view-context key, fix in place, re-run.

- [ ] **Step 3: Run linters & type-check on modified files**

```bash
uv run ruff check apps/service_providers/ --fix
uv run ruff format apps/service_providers/
uv run ty check apps/service_providers/
```

Expected: clean.

- [ ] **Step 4: Commit fixes if any**

```bash
git add -u
git commit -m "fix: resolve issues found in service provider sweep" || true
```

---

## Task 12: Add `ACTION_PARAMS_FORMS` registry and form factory

**Files:**
- Modify: `apps/events/forms.py`

- [ ] **Step 1: Add the registry and helper**

In `apps/events/forms.py`, after `EmptyForm` and before
`ScheduledMessageConfigForm` (or at the end of the file — pick a spot
consistent with surrounding ordering), add:

```python
ACTION_PARAMS_FORMS = {
    "log": EmptyForm,
    "send_message_to_bot": SendMessageToBotForm,
    "end_conversation": EmptyForm,
    "schedule_trigger": ScheduledMessageConfigForm,
    "pipeline_start": PipelineStartForm,
}


def build_action_params_form(action_type, *, data=None, initial=None, team_id, experiment_id):
    """Build the secondary "params" form for a given EventAction action_type."""
    form_cls = ACTION_PARAMS_FORMS[action_type]
    kwargs = {"data": data, "initial": initial}
    if form_cls is ScheduledMessageConfigForm:
        kwargs["experiment_id"] = experiment_id
    elif form_cls is PipelineStartForm:
        kwargs["team_id"] = team_id
    return form_cls(**kwargs)
```

- [ ] **Step 2: Delete `EventActionTypeSelectForm` and `get_action_params_form`**

Remove these blocks from `apps/events/forms.py`:

```python
class EventActionTypeSelectForm(TypeSelectForm):
    def save(self, *args, **kwargs):
        instance = self.primary.save(*args, **kwargs, commit=False)
        instance.params = self.active_secondary().cleaned_data
        instance.save()
        return instance


def get_action_params_form(data=None, instance=None, team_id=None, experiment_id=None):
    ...
```

Drop the `from apps.generics.type_select_form import TypeSelectForm` import
at the top of the file.

- [ ] **Step 3: Run lint**

```bash
uv run ruff check apps/events/forms.py --fix
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add apps/events/forms.py
git commit -m "refactor: replace EventActionTypeSelectForm with action-params registry"
```

---

## Task 13: Add `action_params_form_view` fragment endpoint

**Files:**
- Modify: `apps/events/views.py`
- Modify: `apps/events/urls.py`
- Create: `templates/events/_action_params_form.html`

- [ ] **Step 1: Create partial template**

Write `templates/events/_action_params_form.html`:

```django
{% load form_tags %}
{% if form.fields %}
  {% render_form_fields form %}
{% endif %}
{{ form.non_field_errors }}
```

- [ ] **Step 2: Add the view**

In `apps/events/views.py`, add at module bottom (or alongside other create
views — match local convention):

```python
@login_and_team_required
def action_params_form_view(request, team_slug: str, experiment_id: str):
    """Return the action-params secondary form fragment for ``action_type``.

    Reachable from the same pages that already require event create/change perms.
    """
    action_type = request.GET.get("action_type")
    if action_type not in ACTION_PARAMS_FORMS:
        return HttpResponseBadRequest("Invalid action_type")
    form = build_action_params_form(
        action_type,
        team_id=request.team.id,
        experiment_id=experiment_id,
    )
    return render(request, "events/_action_params_form.html", {"form": form})
```

Add the imports at module top (not inside the function):

```python
from django.http import HttpResponseBadRequest, HttpResponseRedirect

from apps.events.forms import (
    ACTION_PARAMS_FORMS,
    build_action_params_form,
    StaticTriggerForm,
    TimeoutTriggerForm,
)
```

- [ ] **Step 3: Add URL pattern**

In `apps/events/urls.py`, append to `urlpatterns`:

```python
path("action-params/", views.action_params_form_view, name="action_params_form"),
```

- [ ] **Step 4: Commit**

```bash
git add apps/events/views.py apps/events/urls.py templates/events/_action_params_form.html
git commit -m "feat: add action_params_form_view HTMX endpoint"
```

---

## Task 14: Refactor event create / edit views

**Files:**
- Modify: `apps/events/views.py`

- [ ] **Step 1: Rewrite `_create_event_view`**

Replace `_create_event_view` (currently lines 33–51):

```python
def _create_event_view(trigger_form_class, request, team_slug: str, experiment_id: str):
    if request.method == "POST":
        action_type = request.POST.get("action_type") or _default_action_type()
        action_primary_form = EventActionForm(request.POST)
        action_params_form = build_action_params_form(
            action_type,
            data=request.POST,
            team_id=request.team.id,
            experiment_id=experiment_id,
        )
        trigger_form = trigger_form_class(request.POST)

        if (
            action_primary_form.is_valid()
            and action_params_form.is_valid()
            and trigger_form.is_valid()
        ):
            saved_action = action_primary_form.save(experiment_id=experiment_id)
            saved_action.params = action_params_form.cleaned_data
            saved_action.save()
            trigger = trigger_form.save(commit=False, experiment_id=experiment_id)
            trigger.action = saved_action
            trigger.save()
            return HttpResponseRedirect(_get_events_url(team_slug, experiment_id))
    else:
        action_type = _default_action_type()
        action_primary_form = EventActionForm()
        action_params_form = build_action_params_form(
            action_type,
            team_id=request.team.id,
            experiment_id=experiment_id,
        )
        trigger_form = trigger_form_class()

    context = _event_form_context(
        trigger_form, action_primary_form, action_params_form, action_type, trigger_form_class
    )
    return render(request, "events/manage_event.html", context)


def _default_action_type() -> str:
    """First key in ACTION_PARAMS_FORMS."""
    return next(iter(ACTION_PARAMS_FORMS))


def _event_form_context(trigger_form, action_primary_form, action_params_form, action_type, trigger_form_class):
    return {
        "trigger_form": trigger_form,
        "action_primary_form": action_primary_form,
        "action_params_form": action_params_form,
        "action_type": action_type,
        "event_type": trigger_form_class._meta.model._meta.model_name,
    }
```

Add `EventActionForm` to the existing import block at the top.

- [ ] **Step 2: Rewrite `_edit_event_view`**

Replace `_edit_event_view` (currently lines 66–97) with:

```python
def _edit_event_view(trigger_type, request, team_slug: str, experiment_id: str, trigger_id):
    trigger_form_class = {
        "static": StaticTriggerForm,
        "timeout": TimeoutTriggerForm,
    }[trigger_type]
    model_class = {
        "static": StaticTrigger,
        "timeout": TimeoutTrigger,
    }[trigger_type]
    trigger = get_object_or_404(model_class, id=trigger_id, experiment_id=experiment_id)

    if request.method == "POST":
        action_type = request.POST.get("action_type") or trigger.action.action_type
        action_primary_form = EventActionForm(request.POST, instance=trigger.action)
        action_params_form = build_action_params_form(
            action_type,
            data=request.POST,
            initial=trigger.action.params,
            team_id=request.team.id,
            experiment_id=experiment_id,
        )
        trigger_form = trigger_form_class(request.POST, instance=trigger)
        if (
            action_primary_form.is_valid()
            and action_params_form.is_valid()
            and trigger_form.is_valid()
        ):
            saved_action = action_primary_form.save(experiment_id=experiment_id)
            saved_action.params = action_params_form.cleaned_data
            saved_action.save()
            trigger_form.save(experiment_id=experiment_id)
            return HttpResponseRedirect(_get_events_url(team_slug, experiment_id))
    else:
        action_type = trigger.action.action_type
        action_primary_form = EventActionForm(instance=trigger.action)
        action_params_form = build_action_params_form(
            action_type,
            initial=trigger.action.params,
            team_id=request.team.id,
            experiment_id=experiment_id,
        )
        trigger_form = trigger_form_class(instance=trigger)

    context = _event_form_context(
        trigger_form, action_primary_form, action_params_form, action_type, trigger_form_class
    )
    return render(request, "events/manage_event.html", context)
```

- [ ] **Step 3: Update import block**

Replace the existing `from apps.events.forms import (...)` block at the top
of `apps/events/views.py` with:

```python
from apps.events.forms import (
    ACTION_PARAMS_FORMS,
    EventActionForm,
    StaticTriggerForm,
    TimeoutTriggerForm,
    build_action_params_form,
)
```

Drop `get_action_params_form` from the imports.

- [ ] **Step 4: Run lint**

```bash
uv run ruff check apps/events/views.py --fix
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add apps/events/views.py
git commit -m "refactor: compose plain forms in event create/edit views"
```

---

## Task 15: Update `manage_event.html` for HTMX-loaded params form

**Files:**
- Modify: `templates/events/manage_event.html`

- [ ] **Step 1: Replace template**

Overwrite `templates/events/manage_event.html` with:

```django
{% extends "web/app/app_base.html" %}
{% load form_tags %}
{% block app %}
    <div class="app-card max-w-5xl mx-auto">
        <div class="flex">
            <div class="flex-1">
                <h1 class="pg-title">{{ title }}</h1>
            </div>
            <div class="justify-self-end">
                {% block title_bar_end %}
                {% endblock title_bar_end %}
            </div>
        </div>
        <div>
            {% block pre_form %}
            {% endblock pre_form %}
            <form method="post" class="my-2"
                  x-data="formData"
                  x-init="watchTriggerType()">
                {% csrf_token %}
                {% block form %}
                    <h1 class="pg-title">Event Details</h1>
                    {% if event_type == 'statictrigger' %}
                        {% render_field trigger_form.type xmodel="triggerType" %}
                    {% else %}
                        {% render_form_fields trigger_form %}
                    {% endif %}
                    {% render_form_fields action_primary_form %}
                    <div id="action-params"
                         hx-get="{% url 'events:action_params_form' request.team.slug experiment.id %}"
                         hx-trigger="change from:[name=action_type]"
                         hx-include="[name=action_type]"
                         hx-swap="innerHTML">
                        {% include "events/_action_params_form.html" with form=action_params_form %}
                    </div>
                    {{ trigger_form.non_field_errors }}
                    {{ action_primary_form.non_field_errors }}
                {% endblock form %}
                {% block form_actions %}
                    <input type="submit" class="btn btn-primary mt-2" value="Save">
                {% endblock form_actions %}
            </form>
            {% block post_form %}
            {% endblock post_form %}
        </div>
    </div>
    <script>
        document.addEventListener('alpine:init', () => {
            Alpine.data('formData', () => ({
                triggerType: '{{ trigger_form.initial.type }}',

                isActionDisabled(action) {
                    return this.triggerType === 'new_bot_message' &&
                    ['send_message_to_bot', 'schedule_trigger'].includes(action);
                },

                watchTriggerType() {
                    this.$watch('triggerType', value => {
                        const actionSelect = document.getElementById('{{ action_primary_form.action_type.id_for_label }}');
                        if (!actionSelect) return;
                        if (value === 'new_bot_message' &&
                            ['send_message_to_bot', 'schedule_trigger'].includes(actionSelect.value)) {
                                actionSelect.value = 'log';
                                actionSelect.dispatchEvent(new Event('change'));
                            }
                    });
                },

                init() {
                    const actionSelect = document.getElementById('{{ action_primary_form.action_type.id_for_label }}');
                    if (actionSelect) {
                        actionSelect.querySelectorAll('option').forEach(option => {
                            const value = option.value;
                            option.setAttribute('x-bind:disabled', `isActionDisabled('${value}')`);
                            option.setAttribute('x-show', `!isActionDisabled('${value}')`);
                        });
                    }
                }
            }))
        });
    </script>
{% endblock app %}
```

Note three deletions from the previous version:
- The for-loop over `action_form.secondary.items` is gone.
- The `type` Alpine state and `x-model="type"` on the action-type select are
  gone — HTMX listens directly for the native `change` event.
- The `secondary_key` template variable is no longer needed.

- [ ] **Step 2: Verify `experiment` exists in template context**

Check what context `manage_event.html` is rendered with — the existing
`render(request, "events/manage_event.html", context)` calls do not pass
`experiment`. Search for it:

```bash
grep -n '"events/manage_event.html"' apps/events/views.py
```

If `experiment` is not in context but the template URL builder needs it,
add `experiment_id` to context and use `{% url "events:action_params_form"
request.team.slug experiment_id %}`.

Update both `_event_form_context` (Task 14) to include `"experiment_id":
experiment_id` — but `_event_form_context` doesn't currently receive
`experiment_id`. Add it as a parameter:

```python
def _event_form_context(
    trigger_form,
    action_primary_form,
    action_params_form,
    action_type,
    trigger_form_class,
    experiment_id,
):
    return {
        "trigger_form": trigger_form,
        "action_primary_form": action_primary_form,
        "action_params_form": action_params_form,
        "action_type": action_type,
        "event_type": trigger_form_class._meta.model._meta.model_name,
        "experiment_id": experiment_id,
    }
```

Update both call sites in `_create_event_view` and `_edit_event_view` to
pass `experiment_id` (already in scope as a function parameter).

In the template, change the `hx-get` URL accordingly:

```django
hx-get="{% url 'events:action_params_form' request.team.slug experiment_id %}"
```

- [ ] **Step 3: Commit**

```bash
git add apps/events/views.py templates/events/manage_event.html
git commit -m "refactor: load action-params form via HTMX in manage_event"
```

---

## Task 16: Smoke-test events end to end

- [ ] **Step 1: Run event tests**

```bash
uv run pytest apps/events/ -v
```

Expected: all pass.

- [ ] **Step 2: Run linters & type-check**

```bash
uv run ruff check apps/events/ --fix
uv run ruff format apps/events/
uv run ty check apps/events/
```

Expected: clean.

- [ ] **Step 3: Manual smoke check**

Open `templates/events/manage_event.html` and trace through what happens
on a static-trigger create page:

1. Initial GET → renders trigger_form, action_primary_form (with default
   action_type), action_params_form for default type.
2. User changes action_type select → HTMX fires GET to `action_params_form`
   endpoint → `#action-params` swaps to the new form HTML.
3. User picks `new_bot_message` static trigger → Alpine watcher sets the
   action_type to `log` and dispatches change → HTMX reloads the params
   form for `log`.
4. POST submits all fields → view validates the three forms and saves.

Confirm the trace is consistent with the code; no implementation step
required if it is.

- [ ] **Step 4: Commit any fixes**

```bash
git add -u
git commit -m "fix: resolve issues found in event sweep" || true
```

---

## Task 17: Delete `TypeSelectForm` and friends

**Files:**
- Delete: `apps/generics/type_select_form.py`
- Delete: `apps/generics/exceptions.py`
- Delete: `templates/generic/type_select_form.html`
- Modify: `apps/generics/views.py`

- [ ] **Step 1: Verify nothing still imports the dataclass**

```bash
grep -rn "TypeSelectForm\|TypeSelectFormError\|BaseTypeSelectFormView\|generic/type_select_form" \
  apps/ templates/ config/
```

Expected: no matches outside the files about to be deleted.

If any matches remain, fix them before deleting.

- [ ] **Step 2: Delete `apps/generics/type_select_form.py`**

```bash
rm apps/generics/type_select_form.py
```

- [ ] **Step 3: Delete `apps/generics/exceptions.py`**

```bash
rm apps/generics/exceptions.py
```

- [ ] **Step 4: Delete `templates/generic/type_select_form.html`**

```bash
rm templates/generic/type_select_form.html
```

- [ ] **Step 5: Drop `BaseTypeSelectFormView` and unused imports from `apps/generics/views.py`**

In `apps/generics/views.py`, delete the entire `BaseTypeSelectFormView`
class (currently lines 20–92). Then remove imports that are now unused.
After editing, the imports section should drop:

```python
from django.shortcuts import get_object_or_404, redirect, render
from apps.files.forms import get_file_formset
from apps.generics.type_select_form import TypeSelectForm
```

…unless `redirect`, `render`, or `get_object_or_404` are still used by
`paginate_session` / `render_session_details`. Check each before removing
— `render_session_details` uses `TemplateResponse` (not `render`), and
`paginate_session` uses `redirect`, so:

- Keep `redirect`.
- Drop `render` and `get_object_or_404`.
- Drop `get_file_formset`.
- Drop `TypeSelectForm`.

- [ ] **Step 6: Run lint**

```bash
uv run ruff check apps/generics/views.py --fix
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add apps/generics/ templates/generic/
git commit -m "refactor: delete TypeSelectForm and BaseTypeSelectFormView"
```

---

## Task 18: Final verification

- [ ] **Step 1: Run full test suite for affected apps**

```bash
uv run pytest apps/service_providers/ apps/events/ apps/slack/ apps/experiments/ apps/generics/ -v
```

Expected: all pass.

- [ ] **Step 2: Lint and format**

```bash
uv run ruff check apps/ --fix
uv run ruff format apps/
```

Expected: clean.

- [ ] **Step 3: Type check**

```bash
uv run ty check apps/service_providers/ apps/events/ apps/generics/
```

Expected: clean.

- [ ] **Step 4: Search for any leftover references**

```bash
grep -rn "TypeSelectForm\|BaseTypeSelectFormView\|x-bind:disabled\|x-bind:required" apps/ templates/
```

Expected: no matches in `apps/` or `templates/` (other usages of
`x-bind:disabled` / `x-bind:required` may exist in unrelated templates —
inspect each match and confirm it is unrelated to TypeSelectForm).

- [ ] **Step 5: Final commit if anything was fixed**

```bash
git add -u
git commit -m "chore: final cleanup pass" || true
```

---

## Self-Review Notes

- **Spec coverage:** Each spec section maps to tasks: service-provider URL
  routing → Task 3; view → Task 4; template → Tasks 5–6; dropdown →
  Tasks 7–8; subtype filtering → Task 1; backwards compat → Task 9; tests
  → Task 10. Events form refactor → Task 12; fragment endpoint → Task 13;
  view refactor → Task 14; template refactor → Task 15. Cleanup → Task 17.
- **Edit-on-disabled-flag:** Spec calls out that edit must work for
  flag-excluded subtypes. Task 4's `_resolve_subtype` only applies the
  filter on create (when `instance` is None), satisfying this.
- **No placeholders.** Searched plan for "TBD"/"TODO"/"implement later" —
  none.
- **Type consistency:** `get_service_provider_forms` returns
  `(primary_form, config_form)` (Task 2); the view destructures into
  `primary_form, config_form` (Task 4); the template uses `primary_form`
  and `config_form` (Task 5). `ACTION_PARAMS_FORMS` and
  `build_action_params_form` defined in Task 12 are imported in Task 13
  and used in Task 14 with consistent kwargs (`team_id`, `experiment_id`).
