# Dataset "Add Sessions" Page Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify the dataset "Add Sessions" sub-page by removing redundant controls (dead "Filtered" checkbox column, always-visible "Messages to clone" radio) and replacing them with a single conditional Clone toggle inline with the filter bar.

**Architecture:** Server-side template restructure + small JS helper. The POST contract, Celery tasks, and URL routes are unchanged — only the rendered UI and the JS state that drives it change. The Clone-scope toggle (all messages vs. filtered messages) is gated server-side on `evaluation_mode != 'session'` and client-side on `hasActiveFilters` (a new getter on the shared session-selector Alpine component).

**Tech Stack:** Django 5 templates, django-tables2, Alpine.js, daisyUI/Tailwind, pytest + Django's test client.

**Reference spec:** `docs/superpowers/specs/2026-05-25-dataset-add-sessions-simplification-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `apps/evaluations/tables.py` | Definition of `EvaluationSessionsSelectionTable` (the django-tables2 table rendered into the add-sessions sub-page) | Modify: drop the `clone_filtered_only` column and the verbose label on the `selection` column. |
| `assets/javascript/apps/human_annotations/session-selector.js` | Shared Alpine component (`annotationQueueSessionSelector`) used by both the annotation-queue add-sessions page and the dataset add-sessions page. | Modify: add a `hasActiveFilters` getter that returns true iff any param in `filterParams` has a name starting with `filter_`. |
| `templates/evaluations/add_sessions.html` | The add-sessions sub-page template. | Modify: rewrite the controls section above the table — unified action bar, conditional Clone toggle inline with filter bar, single checkbox column hidden when `mode !== 'selected'`. The page-local Alpine extension also gains a watcher that resets `messageScope` to `'all'` when `hasActiveFilters` flips false. |
| `apps/evaluations/tests/test_add_sessions_view.py` | New test file for the view-level UI assertions. | Create: 4 tests covering session-mode vs message-mode × filters-present vs filters-absent. |

---

## Conventions reminder

- Run a single test: `uv run pytest path/to/test.py::test_name -v`
- Run all tests in a file: `uv run pytest path/to/test.py -v`
- Lint Python: `uv run ruff check path/to/file.py --fix`
- Format Python: `uv run ruff format path/to/file.py`
- Build JS bundle (required after editing JS): `npm run dev`
- Lint JS: `npm run lint path/to/file.js`

Commit messages: follow the conventional-commit style already used on this branch (e.g. `feat: ...`, `fix: ...`, `refactor: ...`). Sign-off footer is **not** required by hooks; do not add a `Co-Authored-By` unless instructed.

Pre-commit hooks run on commit (ruff, djLint, eslint, prettier, ty). If a hook auto-fixes a file, the commit will abort — re-stage and re-commit.

---

### Task 1: Drop the dead `clone_filtered_only` column from the selection table

**Context:** The `EvaluationSessionsSelectionTable` renders two checkbox columns ("All" and "Filtered"). The "Filtered" column references `js_function="updateFilteredSessions()"` / `toggleFilteredSessions()`, which only exist in the legacy `dataset-mode-selector.js` bundle. The add-sessions page loads `human_annotations-bundle.js`, which has no such handlers — the column is dead UI. The table is also used by `DatasetSessionsSelectionTableView` (the legacy inline `dataset_sessions_selection_list` endpoint preserved for backward compat; #3428 will clean it up). Verify nothing else relies on the "Filtered" column.

**Files:**
- Modify: `apps/evaluations/tables.py:268-326`
- Modify (cleanup): any test that references the removed column — search first.

- [ ] **Step 1: Verify no consumer depends on the `Filtered` column**

Run:
```bash
grep -rn "clone_filtered_only\|filter-checkbox\|Filtered\"" \
  apps/evaluations \
  templates/evaluations \
  assets/javascript/apps/human_annotations \
  --include="*.py" --include="*.html" --include="*.js"
```

Expected: only references inside `apps/evaluations/tables.py` (the column itself). `assets/javascript/apps/evaluations/dataset-mode-selector.js` references `filter-checkbox` but that's the legacy bundle — leave it alone (cleanup is #3428's job).

If anything else lights up under `templates/evaluations/` or `apps/evaluations/views/`, stop and investigate before continuing.

- [ ] **Step 2: Write a failing table-render test**

Create `apps/evaluations/tests/test_evaluation_sessions_selection_table.py` with:

```python
import pytest

from apps.evaluations.tables import EvaluationSessionsSelectionTable
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_table_has_single_selection_column():
    """The Add Sessions table should expose only one checkbox column."""
    ExperimentSessionFactory.create()
    table = EvaluationSessionsSelectionTable(ExperimentSession.objects.all())
    column_names = list(table.columns.names())
    assert "selection" in column_names
    assert "clone_filtered_only" not in column_names


@pytest.mark.django_db()
def test_selection_column_header_has_no_label():
    """The remaining selection column header should be unlabeled (bare checkbox)."""
    ExperimentSessionFactory.create()
    table = EvaluationSessionsSelectionTable(ExperimentSession.objects.all())
    assert table.columns["selection"].header == ""
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_evaluation_sessions_selection_table.py -v`

Expected: both tests FAIL — first because `clone_filtered_only` is still in `columns.names()`, second because the header currently reads `"All"`.

- [ ] **Step 4: Edit `apps/evaluations/tables.py`**

In `apps/evaluations/tables.py`, replace the `selection` and `clone_filtered_only` column definitions (lines 269–299) with a single column. The whole `EvaluationSessionsSelectionTable` block becomes:

```python
class EvaluationSessionsSelectionTable(tables.Table):
    selection = TemplateColumnWithCustomHeader(
        template_name="evaluations/session_checkbox.html",
        verbose_name="",
        orderable=False,
        extra_context={
            "css_class": "checkbox checkbox-primary session-checkbox",
            "js_function": "updateSelectedSessions()",
        },
        header_template="evaluations/session_checkbox.html",
        header_context={
            "js_function": "toggleSelectedSessions()",
            "css_class": "checkbox checkbox-primary session-checkbox",
        },
    )
    experiment = columns.Column(accessor="experiment", verbose_name="Experiment", order_by="experiment__name")
    participant = columns.Column(accessor="participant", verbose_name="Participant", order_by="participant__identifier")
    last_message = columns.Column(accessor="last_activity_at", verbose_name="Last Message", orderable=True)
    versions = ArrayColumn(verbose_name="Versions", accessor="experiment_versions", orderable=False)
    message_count = columns.Column(accessor="message_count", verbose_name="Messages", orderable=False)
    session = actions.ActionsColumn(
        actions=[
            chip_action(
                label="View Session",
                url_factory=_chip_session_url_factory,
                open_url_in_new_tab=True,
            ),
        ],
        orderable=True,
    )

    class Meta:
        model = ExperimentSession
        fields = []
        row_attrs = {
            **settings.DJANGO_TABLES2_ROW_ATTRS,
            "data-redirect-target": "_blank",
        }
        attrs = {"class": "table w-full"}
        orderable = False
        empty_text = "No sessions available for selection."
```

The changes vs. existing code:
- `selection` column: `verbose_name="All"` → `verbose_name=""`; removed the `header_context["help_content"]` key (so the tooltip help-icon won't render).
- `clone_filtered_only` column: deleted entirely.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest apps/evaluations/tests/test_evaluation_sessions_selection_table.py -v`

Expected: both tests PASS.

- [ ] **Step 6: Re-run the existing eval tests to catch regressions**

Run: `uv run pytest apps/evaluations/tests/ -v -x`

Expected: all pass. (Existing tests in `test_evaluation_dataset_session_clone.py` only test the Celery task, not the table, so they should be unaffected. `test_import_from_annotation_queue.py` has add-sessions view tests — those should still pass because they don't assert on the removed column.)

- [ ] **Step 7: Lint and format**

Run:
```bash
uv run ruff check apps/evaluations/tables.py apps/evaluations/tests/test_evaluation_sessions_selection_table.py --fix
uv run ruff format apps/evaluations/tables.py apps/evaluations/tests/test_evaluation_sessions_selection_table.py
```

- [ ] **Step 8: Commit**

```bash
git add apps/evaluations/tables.py apps/evaluations/tests/test_evaluation_sessions_selection_table.py
git commit -m "refactor(evaluations): drop dead 'Filtered' column from Add Sessions table"
```

---

### Task 2: Add `hasActiveFilters` getter to the session-selector Alpine component

**Context:** The shared Alpine component (`annotationQueueSessionSelector`) syncs the URL query string into `filterParams` whenever filters change (via the `filter:change` window event dispatched by `templates/filters/_filter_component_alpine.html:345`). It does not currently expose a derived "are there any active filters?" signal. We add a small getter that the page template can use in an `x-show` binding to gate the Clone toggle.

The convention for filter-related URL params in this codebase is the `filter_` prefix (see `apps/evaluations/views/dataset_views.py` `CreateDataset.get_initial`: `any(key.startswith("filter_") for key in self.request.GET)`).

This component is also used by the annotation-queue add-sessions page. The new getter is purely additive — no behavior change for existing consumers.

**Files:**
- Modify: `assets/javascript/apps/human_annotations/session-selector.js`

- [ ] **Step 1: Read the current component**

Re-read `assets/javascript/apps/human_annotations/session-selector.js` (lines 12–48) so you understand where `filterParams` is set and where the existing getters live.

- [ ] **Step 2: Add the getter**

Insert the following getter immediately after the existing `get pillText()` block (around line 46), before `get pillClass()`:

```javascript
    get hasActiveFilters() {
      return this.filterParams.some((p) => p.name.startsWith('filter_'));
    },
```

- [ ] **Step 3: Lint the JS file**

Run: `npm run lint assets/javascript/apps/human_annotations/session-selector.js`

Expected: no errors.

- [ ] **Step 4: Rebuild the bundle so the new getter is available in dev**

Run: `npm run dev`

Expected: webpack rebuilds without errors. The `human_annotations-bundle.js` in `static/js/` should now contain the new getter.

- [ ] **Step 5: Smoke check the bundle**

Run: `grep -c "hasActiveFilters" static/js/human_annotations-bundle.js`

Expected: at least `1` (proves the rebuild included the change).

- [ ] **Step 6: Commit**

```bash
git add assets/javascript/apps/human_annotations/session-selector.js static/js/human_annotations-bundle.js
git commit -m "feat(evaluations): expose hasActiveFilters getter on session-selector"
```

Note: only commit the `static/js/` bundle if this repository's convention is to commit built bundles. Check first with `git status` — if the bundle shows as untracked or the existing bundle is gitignored, skip it. (If `static/js/human_annotations-bundle.js` was already tracked, commit it.)

Verify: `git ls-files static/js/human_annotations-bundle.js` — if it returns a path, the file is tracked and should be committed; otherwise omit it from the `git add`.

---

### Task 3: Rewrite the Add Sessions template with the unified action bar

**Context:** This is the central change. We rewrite the entire block between `<div class="flex flex-col gap-4">` and the form's hidden inputs to:
1. Put the Clone toggle inline-right of the filter bar (server-rendered only when `dataset.evaluation_mode != 'session'`, client-shown only when `hasActiveFilters`).
2. Merge "Add:" pills + count + primary button + Cancel into one sentence-row.
3. Drop the always-on "Messages to clone" radio bar.
4. Drop the "Row selections do not affect this mode" hint and the table dimming. Instead, hide the table's checkbox column entirely when `mode !== 'selected'` using an Alpine class binding on the table container that toggles a CSS rule.

Because the table is HTMX-loaded into `#sessions-table` and django-tables2 renders the full `<table>` server-side, we hide the checkbox column by toggling a CSS class on the wrapper that uses a descendant selector to hide the `th`/`td` of the selection column. The selection column doesn't have a unique data attribute today — we add one in the table definition (Task 1 step 4 already keeps `selection` as the first column; we'll target it positionally with `:first-child` since the selection column is always the leftmost). This avoids touching `session_checkbox.html`.

The page-local Alpine extension at the bottom of the template gains:
- a `messageScope` field (existing).
- a `$watch` on `hasActiveFilters`: when it goes from truthy to falsy, reset `messageScope = 'all'`.

**Files:**
- Modify: `templates/evaluations/add_sessions.html`
- Create: `apps/evaluations/tests/test_add_sessions_view.py` (the view-level tests for this task)

- [ ] **Step 1: Write failing view-level tests**

Create `apps/evaluations/tests/test_add_sessions_view.py`:

```python
import pytest
from django.test import Client
from django.urls import reverse

from apps.evaluations.models import EvaluationDataset, EvaluationMode
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def user(team_with_users):
    return team_with_users.members.first()


@pytest.fixture()
def client_with_user(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture()
def session_dataset(team_with_users):
    return EvaluationDataset.objects.create(
        team=team_with_users, name="Session DS", evaluation_mode=EvaluationMode.SESSION
    )


@pytest.fixture()
def message_dataset(team_with_users):
    return EvaluationDataset.objects.create(
        team=team_with_users, name="Message DS", evaluation_mode=EvaluationMode.MESSAGE
    )


def _add_sessions_url(team, dataset):
    return reverse("evaluations:dataset_add_sessions", args=[team.slug, dataset.pk])


@pytest.mark.django_db()
def test_session_mode_dataset_has_no_clone_toggle(client_with_user, team_with_users, session_dataset):
    """Session-mode datasets never show the Clone toggle."""
    response = client_with_user.get(_add_sessions_url(team_with_users, session_dataset))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="message_scope_ui"' not in content
    assert "messageScope" not in content or "x-model=\"messageScope\"" not in content


@pytest.mark.django_db()
def test_session_mode_dataset_has_no_old_messages_to_clone_row(client_with_user, team_with_users, session_dataset):
    """The legacy 'Messages to clone' bar must be gone for all dataset modes."""
    response = client_with_user.get(_add_sessions_url(team_with_users, session_dataset))
    assert response.status_code == 200
    assert "Messages to clone" not in response.content.decode()


@pytest.mark.django_db()
def test_message_mode_dataset_renders_clone_toggle_markup(client_with_user, team_with_users, message_dataset):
    """Message-mode datasets render the Clone toggle markup (client-side x-show controls visibility)."""
    response = client_with_user.get(_add_sessions_url(team_with_users, message_dataset))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'x-model="messageScope"' in content
    assert "All messages" in content
    assert "Filtered messages only" in content
    # Visibility is gated client-side on hasActiveFilters
    assert 'x-show="hasActiveFilters"' in content


@pytest.mark.django_db()
def test_message_mode_dataset_has_no_old_messages_to_clone_row(client_with_user, team_with_users, message_dataset):
    response = client_with_user.get(_add_sessions_url(team_with_users, message_dataset))
    assert response.status_code == 200
    # The new label is "Clone:" (inline), not "Messages to clone:" (legacy banner)
    assert "Messages to clone" not in response.content.decode()


@pytest.mark.django_db()
def test_unified_action_bar_renders_for_all_dataset_modes(client_with_user, team_with_users, session_dataset):
    """All three Add-mode pills + count + primary action all live in one row labeled 'Add to dataset:'."""
    response = client_with_user.get(_add_sessions_url(team_with_users, session_dataset))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Add to dataset" in content
    # No more "Row selections do not affect this mode" hint
    assert "Row selections do not affect this mode" not in content


@pytest.mark.django_db()
def test_post_without_message_scope_defaults_to_all(
    client_with_user, team_with_users, message_dataset
):
    """If the Clone toggle is hidden (no filters), the form still posts a usable default."""
    # Hidden input on the form has :value="messageScope" which defaults to 'all'.
    # Server should accept missing/empty message_scope and treat as 'all'.
    response = client_with_user.post(
        _add_sessions_url(team_with_users, message_dataset),
        {"mode": "selected", "session_ids": "", "message_scope": ""},
    )
    # No sessions selected, server redirects back with an error — that's fine, we're only
    # asserting the view doesn't crash on an empty message_scope value.
    assert response.status_code == 302
```

- [ ] **Step 2: Run tests — confirm they fail for the right reasons**

Run: `uv run pytest apps/evaluations/tests/test_add_sessions_view.py -v`

Expected:
- `test_session_mode_dataset_has_no_clone_toggle` — may pass or fail depending on current state (current template wraps the radio in `{% if dataset.evaluation_mode != 'session' %}`, so it likely PASSES already. That's fine.)
- `test_session_mode_dataset_has_no_old_messages_to_clone_row` — FAIL (current template has "Messages to clone:" in the message-mode block; for session-mode this likely PASSES. Adjust if so.)
- `test_message_mode_dataset_renders_clone_toggle_markup` — FAIL (current markup has `x-model="messageScope"` but uses different surrounding markup; `x-show="hasActiveFilters"` does NOT appear yet).
- `test_message_mode_dataset_has_no_old_messages_to_clone_row` — FAIL (current template has the phrase).
- `test_unified_action_bar_renders_for_all_dataset_modes` — FAIL (current template doesn't have the string "Add to dataset" and still has "Row selections do not affect this mode").
- `test_post_without_message_scope_defaults_to_all` — likely PASSES (the view already handles missing values by defaulting to "all" — see `apps/evaluations/views/dataset_views.py:876`).

The crucial fails are the three listed above. If you don't see them, re-read the current template and adjust your assertions before continuing.

- [ ] **Step 3: Rewrite `templates/evaluations/add_sessions.html`**

Replace the entire contents of `templates/evaluations/add_sessions.html` with:

```django
{% extends "web/app/app_base.html" %}
{% load i18n static %}

{% block breadcrumbs %}
  <div class="text-sm breadcrumbs" aria-label="breadcrumbs">
    <ul>
      <li><a href="{% url 'evaluations:dataset_home' request.team.slug %}">{% translate "Datasets" %}</a></li>
      <li><a href="{% url 'evaluations:dataset_edit' request.team.slug dataset.pk %}">{{ dataset.name }}</a></li>
      <li class="pg-breadcrumb-active" aria-current="page">{% translate "Add Sessions" %}</li>
    </ul>
  </div>
{% endblock breadcrumbs %}

{% block app %}
<div x-data="annotationQueueSessionSelector({ sessionCountUrl: '{{ sessions_count_url }}' })"
     x-init="init()"
     data-queue-name="{{ dataset.name }}"
     :class="{ 'hide-selection-col': mode !== 'selected' }">

  <div class="flex flex-col gap-4">
    <h2 class="text-xl font-bold">{% blocktranslate with name=dataset.name %}Add Sessions to "{{ name }}"{% endblocktranslate %}</h2>

    <!-- Error messages -->
    <div x-show="errorMessages.length" class="alert alert-error mb-4" x-cloak>
      <i class="fa-solid fa-exclamation-triangle"></i>
      <div>
        <template x-for="error in errorMessages">
          <p x-text="error"></p>
        </template>
      </div>
    </div>

    <!-- Row 1: filter bar (left) + Clone toggle (right, conditional) -->
    <div class="flex items-center justify-between flex-wrap gap-2">
      <div>
        {% include "experiments/filters.html" %}
      </div>
      {% if dataset.evaluation_mode != 'session' %}
      <div x-show="hasActiveFilters" x-cloak class="flex items-center gap-3 text-sm">
        <span class="font-medium">{% translate "Clone" %}:</span>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="radio" name="message_scope_ui" value="all" x-model="messageScope" class="radio radio-sm radio-primary">
          <span>{% translate "All messages" %}</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="radio" name="message_scope_ui" value="filtered" x-model="messageScope" class="radio radio-sm radio-primary">
          <span>{% translate "Filtered messages only" %}</span>
        </label>
      </div>
      {% endif %}
    </div>

    <!-- Row 2: unified action bar (sentence-style) -->
    <div class="flex items-center flex-wrap gap-3 bg-base-200 rounded-lg px-4 py-2">
      <span class="text-sm font-medium">{% translate "Add to dataset" %}:</span>

      <div class="join">
        <button type="button"
                class="join-item btn btn-sm"
                :class="mode === 'selected' ? 'btn-primary' : 'btn-ghost'"
                @click="setMode('selected')">
          {% translate "Selected" %} (<span x-text="selectedSessionIds.size"></span>)
        </button>
        <button type="button"
                class="join-item btn btn-sm"
                :class="mode === 'all_matching' ? 'btn-primary' : 'btn-ghost'"
                @click="setMode('all_matching')">
          {% translate "All" %} <span x-text="totalCount"></span> {% translate "matching" %}
        </button>
        {% if dataset.evaluation_mode != 'session' %}
        <button type="button"
                class="join-item btn btn-sm"
                :class="mode === 'sample' ? 'btn-primary' : 'btn-ghost'"
                @click="setMode('sample')">
          {% translate "Sample" %} <span x-text="samplePercent"></span>%
        </button>
        {% endif %}
      </div>

      <!-- Sample percent inputs (visible only in sample mode) -->
      <template x-if="mode === 'sample'">
        <div class="flex items-center gap-2">
          <input type="number"
                 x-model.number="samplePercent"
                 @change="clampSamplePercent()"
                 min="1" max="100"
                 class="input input-sm input-bordered w-16 text-center">
          <span class="text-sm">%</span>
          <input type="range"
                 x-model.number="samplePercent"
                 @change="clampSamplePercent()"
                 min="1" max="100"
                 class="range range-sm range-primary w-32">
        </div>
      </template>

      <div class="ml-auto flex items-center gap-3">
        <span class="text-sm text-base-content/70">
          <span x-text="totalCount"></span> {% translate "sessions" %}
        </span>
        <button type="submit" form="add-sessions-form" class="btn btn-primary btn-sm" :disabled="isSubmitDisabled">
          <span x-text="buttonLabel">{% translate "Add to Dataset" %}</span>
        </button>
        <a href="{% url 'evaluations:dataset_edit' team_slug=request.team.slug pk=dataset.pk %}"
           class="btn btn-ghost btn-sm">
          {% translate "Cancel" %}
        </a>
      </div>
    </div>

    <!-- Selected-mode helpers -->
    <div x-show="mode === 'selected' && selectedSessionIds.size === 0" x-cloak
         class="text-sm text-warning">
      {% translate "Select at least one session below." %}
    </div>
    <div x-show="mode === 'selected' && selectedSessionIds.size > 0" x-cloak class="text-sm">
      <span x-text="selectedSessionIds.size"></span> {% translate "selected" %}
      <button type="button" class="btn btn-xs btn-outline ml-2" @click="clearAllSelections()">
        {% translate "Clear" %}
      </button>
    </div>

    <!-- Sample mode hint -->
    <div x-show="mode === 'sample'" x-cloak class="text-xs text-base-content/50">
      {% translate "Sessions are randomly sampled at the time of adding." %}
    </div>

    <!-- Sessions table (HTMX lazy-loaded). When mode !== 'selected', the leftmost
         (selection) column is hidden via CSS on the parent .hide-selection-col class. -->
    <div id="sessions-table"
         data-url="{% url 'evaluations:dataset_add_sessions_table' request.team.slug dataset.pk %}">
      {% include "table/table_placeholder.html" %}
    </div>

    <!-- Submission form -->
    <form method="post" id="add-sessions-form" @submit="handleSubmit($event)">
      {% csrf_token %}
      <input type="hidden" name="session_ids" x-model="sessionIdsString">
      <input type="hidden" name="mode" :value="mode">
      <input type="hidden" name="sample_percent" :value="samplePercent">
      <input type="hidden" name="message_scope" :value="messageScope">
      <template x-for="param in filterParams" :key="param.name">
        <input type="hidden" :name="param.name" :value="param.value">
      </template>
    </form>
  </div>

  <!-- Confirmation modal -->
  <div x-show="showConfirmModal" x-cloak
       class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
       @keydown.escape.window="cancelConfirm()">
    <div class="bg-base-100 rounded-lg shadow-xl p-6 max-w-md w-full mx-4" @click.outside="cancelConfirm()">
      <h3 class="text-lg font-bold mb-2" x-text="confirmTitle"></h3>
      <p class="text-sm text-base-content/70 mb-4" x-text="confirmMessage"></p>
      <div class="flex gap-2 justify-end">
        <button type="button" class="btn btn-ghost btn-sm" @click="cancelConfirm()">
          {% translate "Cancel" %}
        </button>
        <button type="button" class="btn btn-primary btn-sm" @click="confirmSubmit()">
          {% translate "Confirm" %}
        </button>
      </div>
    </div>
  </div>

</div>

<style>
  /* Hide the leftmost (selection) column when not in 'selected' mode.
     The selection column is always the first column of the rendered table. */
  .hide-selection-col #sessions-table table th:first-child,
  .hide-selection-col #sessions-table table td:first-child {
    display: none;
  }
</style>
{% endblock app %}

{% block page_js %}
  <script src="{% static 'js/human_annotations-bundle.js' %}"></script>
  <script>
    // Extend the shared annotationQueueSessionSelector with messageScope state
    // and a watcher that resets it whenever filters clear.
    const _baseSelector = window.annotationQueueSessionSelector;
    window.annotationQueueSessionSelector = function(options) {
      const base = _baseSelector(options);
      const baseInit = base.init.bind(base);
      return Object.assign(base, {
        messageScope: 'all',
        init() {
          baseInit();
          this.$watch('hasActiveFilters', (now) => {
            if (!now && this.messageScope !== 'all') {
              this.messageScope = 'all';
            }
          });
        },
      });
    };
  </script>
{% endblock page_js %}
```

Key things that changed vs. the previous template:
- The outer `x-data` div has a `:class="{ 'hide-selection-col': mode !== 'selected' }"` binding.
- The filter row now has the Clone toggle as a conditional sibling (server-gated on `evaluation_mode != 'session'`, client-shown on `hasActiveFilters`).
- The old `Messages to clone` bar (line 100–113) is gone.
- The `Add:` bar plus the separate count/button row are merged into one unified action bar with sentence-style labels.
- The old "Row selections do not affect this mode" hint and the dimming on `#sessions-table` are gone.
- The bottom `<form>` no longer renders duplicate Add/Cancel buttons (the only primary button now lives in the unified action bar). The form remains and still receives the hidden inputs because `<button type="submit" form="add-sessions-form">` references it by id.
- A small `<style>` block hides the first column of the rendered table when `.hide-selection-col` is on the wrapper.
- The page-local `<script>` extends `init()` to register a `$watch` on `hasActiveFilters` that resets `messageScope` to `'all'`.

- [ ] **Step 4: Run the new view-level tests**

Run: `uv run pytest apps/evaluations/tests/test_add_sessions_view.py -v`

Expected: all six tests PASS.

- [ ] **Step 5: Run the full evaluations test suite to catch regressions**

Run: `uv run pytest apps/evaluations/tests/ -v -x`

Expected: all pass. Pay particular attention to `test_import_from_annotation_queue.py::test_eval_dataset_add_sessions_post_selected` (still passes — POST contract unchanged) and `test_eval_dataset_add_sessions_get` (still passes — context keys unchanged).

- [ ] **Step 6: Lint / format / typecheck**

Run:
```bash
uv run ruff check apps/evaluations/tests/test_add_sessions_view.py --fix
uv run ruff format apps/evaluations/tests/test_add_sessions_view.py
uv run ty check apps/evaluations/tests/test_add_sessions_view.py
```

The template file is linted via djLint on commit (pre-commit hook). If the commit aborts with djLint changes, re-stage and re-commit.

- [ ] **Step 7: Commit**

```bash
git add templates/evaluations/add_sessions.html apps/evaluations/tests/test_add_sessions_view.py
git commit -m "refactor(evaluations): simplify Add Sessions page UI

- Unify Add-mode pills, count, and primary action into one sentence-style row
- Move all-vs-filtered messages choice inline with filter bar; show only for
  message-mode datasets with at least one active filter
- Drop dead 'Messages to clone' radio and 'Row selections don't affect this
  mode' hint; hide the table's checkbox column when not in 'selected' mode
- Add page-local watcher that resets messageScope to 'all' when filters clear"
```

---

### Task 4: Manual verification in the dev server

**Context:** The automated tests cover server-rendered markup, but Alpine reactivity (the `x-show` toggle, the `$watch` that resets `messageScope`, the column-hide CSS) only manifests in the browser. Spend a few minutes clicking through each combination on a seeded team.

**Files:** none — this is a verification task.

- [ ] **Step 1: Start the dev server**

Run: `uv run inv runserver` (in one terminal) and `npm run dev` (in another, if not already running).

- [ ] **Step 2: Seed (or reuse) a team with at least one session-mode dataset, one message-mode dataset, and a few sessions across two experiments**

If you don't have one handy, the simplest path is: log in, create a dataset of each evaluation_mode via the UI, and ensure you have ≥ 2 sessions from at least 2 different experiments in the team. (Test factories aren't useful here because they don't persist into the dev DB.)

- [ ] **Step 3: Walk through the matrix**

For each row in the table below, navigate to the Add Sessions sub-page (Datasets → dataset name → Add Sessions) and verify the listed expectations.

| Dataset mode | Filters active? | Expected page state |
|---|---|---|
| Session-mode | No filters | No Clone toggle anywhere on the page. Action bar shows 2 pills (no Sample) for session-mode? — check current behavior: the existing template only suppresses Sample for session-mode. Verify the new template matches: Selected + All matching pills only for session-mode. |
| Session-mode | 1 filter set | Same as above — no Clone toggle. Pill counts update. |
| Message-mode | No filters | Clone toggle markup present but hidden (`x-cloak`/`x-show` keeps it off-screen). Action bar shows 3 pills (Selected / All matching / Sample). |
| Message-mode | 1 filter set | Clone toggle visible inline-right of the filter bar, defaulting to "All messages". Switching to "Filtered messages only" updates the radio. Clearing all filters via the X button hides the toggle and resets it back to "All". |

- [ ] **Step 4: Test the submit flow end-to-end (each mode)**

For a message-mode dataset:
- Selected mode: tick one row, click "Add". Verify redirect to `dataset_edit` with success flash. Confirm the message(s) appear in the dataset table once the Celery task finishes.
- All matching mode (with a filter set): click All matching, click Add. Verify confirmation modal appears, click Confirm, verify success.
- Sample mode (set 20%, with at least 5 sessions visible): click Add, verify confirmation modal (if > 200 estimated), click Confirm, verify success.

For each: open the dataset and confirm the cloned messages match the chosen message scope (all messages vs. filter-only messages).

- [ ] **Step 5: Visually confirm the table checkbox column hides/shows cleanly**

Switch between Selected → All matching → Sample → Selected. The leftmost (selection) column should disappear and reappear without layout flicker. The rest of the table layout should be unaffected.

- [ ] **Step 6: Confirm `Cancel` returns to the dataset edit page**

- [ ] **Step 7: Commit any tweaks made during manual verification**

If you adjusted spacing, copy, or CSS in the template based on what you saw, lint and commit those fixups with a clear message (e.g. `style(evaluations): polish Add Sessions spacing after manual review`).

---

## Self-Review (writer pass)

- [x] **Spec coverage:** Every decision in the spec maps to a task. Removing `clone_filtered_only` → Task 1. `hasActiveFilters` getter → Task 2. Template rewrite (action bar + conditional Clone toggle + checkbox column hide + watcher) → Task 3. Manual verification covering all combinations → Task 4. POST contract unchanged → covered by re-running the existing POST tests at the end of Task 3 step 5.

- [x] **Placeholder scan:** No TBD/TODO; all code blocks are concrete.

- [x] **Type consistency:** `messageScope` (camelCase JS) and `message_scope` (snake_case form field) are used consistently. The CSS class name `hide-selection-col` is identical across the `<style>` block and the `:class` binding. The `hasActiveFilters` getter name matches between Task 2 (definition), Task 3 (usage), and the spec.

- [x] **Spec requirement check:** The spec called for `verbose_name=""` on the selection column to drop the "All" header label — implemented in Task 1 step 4 and asserted in Task 1 step 2. The spec called for resetting `messageScope` to `'all'` when filters clear — implemented in Task 3 step 3 via the `$watch` callback.

---

## Out of scope (explicit reminders)

- Do **not** touch `assets/javascript/apps/evaluations/dataset-mode-selector.js` or `static/js/evaluations-bundle.js`. These are legacy and are scheduled for cleanup in #3428.
- Do **not** modify the server-side POST handler in `apps/evaluations/views/dataset_views.py`. The contract is unchanged.
- Do **not** modify the Celery tasks (`create_dataset_from_sessions_task`, `create_dataset_from_session_messages_task`).
- Do **not** remove or alter the `dataset_sessions_selection_json` endpoint.
- Do **not** add per-row clone scope; the design picks one global control.
