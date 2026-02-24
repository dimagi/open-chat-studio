# Annotation Queue Session Selector Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the basic checkbox list in "Add Sessions to Queue" with a filterable, paginated session table matching the evaluations dataset creation UX.

**Architecture:** New annotation-queue-specific table class, two new views (HTML table + JSON session IDs), a focused Alpine.js component (`annotationQueueSessionSelector`), and an updated template. The POST handler is updated to accept comma-separated `external_id` values from a hidden field instead of internal IDs from individual checkboxes.

**Tech Stack:** Django, django-tables2, Alpine.js, HTMX, webpack (legacy UMD bundle), pytest

---

### Task 1: `AnnotationSessionsSelectionTable` table class

**Files:**
- Modify: `apps/human_annotations/tables.py`

**Step 1: Write the failing test**

Add to `apps/human_annotations/tests/test_views.py`:

```python
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.human_annotations.tables import AnnotationSessionsSelectionTable


@pytest.mark.django_db()
def test_annotation_sessions_selection_table_has_selection_column(team_with_users):
    session = ExperimentSessionFactory(team=team_with_users)
    table = AnnotationSessionsSelectionTable([session])
    assert "selection" in table.columns
    assert "experiment" in table.columns
    assert "participant" in table.columns
    assert "message_count" in table.columns
```

**Step 2: Run test to verify it fails**

```bash
pytest apps/human_annotations/tests/test_views.py::test_annotation_sessions_selection_table_has_selection_column -v
```

Expected: `ImportError` — `AnnotationSessionsSelectionTable` does not exist yet.

**Step 3: Add the table class**

At the top of `apps/human_annotations/tables.py`, add imports (check existing imports and add only what's missing):

```python
from django.conf import settings
from django.urls import reverse
from django_tables2 import columns, tables

from apps.experiments.models import ExperimentSession
from apps.generics import actions
from apps.generics.actions import chip_action
from apps.generics.tables import TemplateColumnWithCustomHeader
from apps.teams.utils import get_slug_for_team
```

At the bottom of `apps/human_annotations/tables.py`, add:

```python
def _annotation_session_url_factory(_, request, record, __):
    return reverse(
        "chatbots:chatbot_session_view",
        args=[get_slug_for_team(record.team_id), record.experiment.public_id, record.external_id],
    )


class AnnotationSessionsSelectionTable(tables.Table):
    selection = TemplateColumnWithCustomHeader(
        template_name="evaluations/session_checkbox.html",
        verbose_name="Select",
        orderable=False,
        extra_context={
            "css_class": "checkbox checkbox-primary session-checkbox",
            "js_function": "updateSelectedSessions()",
        },
        header_template="evaluations/session_checkbox.html",
        header_context={
            "help_content": "Select all sessions on this page",
            "js_function": "toggleSelectedSessions()",
            "css_class": "checkbox checkbox-primary session-checkbox",
        },
    )
    experiment = columns.Column(accessor="experiment", verbose_name="Experiment", order_by="experiment__name")
    participant = columns.Column(accessor="participant", verbose_name="Participant", order_by="participant__identifier")
    last_message = columns.Column(accessor="last_activity_at", verbose_name="Last Message", orderable=True)
    message_count = columns.Column(accessor="message_count", verbose_name="Messages", orderable=False)
    session = actions.ActionsColumn(
        actions=[
            chip_action(
                label="View Session",
                url_factory=_annotation_session_url_factory,
                open_url_in_new_tab=True,
            ),
        ],
        orderable=False,
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
        empty_text = "No sessions available."
```

**Step 4: Run test to verify it passes**

```bash
pytest apps/human_annotations/tests/test_views.py::test_annotation_sessions_selection_table_has_selection_column -v
```

Expected: PASS

**Step 5: Lint**

```bash
ruff check apps/human_annotations/tables.py --fix
ruff format apps/human_annotations/tables.py
```

**Step 6: Commit**

```bash
git add apps/human_annotations/tables.py apps/human_annotations/tests/test_views.py
git commit -m "feat: add AnnotationSessionsSelectionTable for queue session selector"
```

---

### Task 2: New views — sessions table + sessions JSON

**Files:**
- Modify: `apps/human_annotations/views/queue_views.py`
- Test: `apps/human_annotations/tests/test_views.py`

**Step 1: Write the failing tests**

Add to `apps/human_annotations/tests/test_views.py`:

```python
import uuid
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_queue_sessions_table_view(client, team_with_users, queue):
    ExperimentSessionFactory.create_batch(3, team=team_with_users)
    url = reverse("human_annotations:queue_sessions_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_queue_sessions_table_only_shows_team_sessions(client, team_with_users, queue):
    own_session = ExperimentSessionFactory(team=team_with_users)
    other_session = ExperimentSessionFactory()  # different team
    url = reverse("human_annotations:queue_sessions_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    content = response.content.decode()
    assert str(own_session.external_id) in content
    assert str(other_session.external_id) not in content


@pytest.mark.django_db()
def test_queue_sessions_json_returns_external_ids(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(3, team=team_with_users)
    ExperimentSessionFactory()  # different team — must not appear
    url = reverse("human_annotations:queue_sessions_json", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    expected_ids = {str(s.external_id) for s in sessions}
    assert set(str(i) for i in data) == expected_ids


@pytest.mark.django_db()
def test_queue_sessions_json_requires_login(team_with_users, queue):
    c = Client()  # unauthenticated
    url = reverse("human_annotations:queue_sessions_json", args=[team_with_users.slug, queue.pk])
    response = c.get(url)
    assert response.status_code in (302, 403)
```

**Step 2: Run tests to verify they fail**

```bash
pytest apps/human_annotations/tests/test_views.py::test_queue_sessions_table_view apps/human_annotations/tests/test_views.py::test_queue_sessions_json_returns_external_ids -v
```

Expected: `NoReverseMatch` — URLs do not exist yet.

**Step 3: Add required imports to `queue_views.py`**

At the top of `apps/human_annotations/views/queue_views.py`, add/update imports (add only what is missing):

```python
from django.http import HttpResponse, JsonResponse

from django_tables2 import LazyPaginator, SingleTableView

from apps.experiments.filters import ExperimentSessionFilter, get_filter_context_data
from apps.filters.models import FilterSet
from apps.teams.decorators import login_and_team_required
from apps.web.dynamic_filters.datastructures import FilterParams
from django.db.models import Count
from django.db.models.functions import Coalesce

from ..tables import AnnotationItemTable, AnnotationQueueTable, AnnotationSessionsSelectionTable
```

**Step 4: Add the two new views to `queue_views.py`**

Add after `AnnotationQueueItemsTableView`:

```python
class AnnotationQueueSessionsTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    """Filterable, paginated session table for selecting sessions to add to a queue."""

    model = ExperimentSession
    table_class = AnnotationSessionsSelectionTable
    template_name = "table/single_table_lazy_pagination.html"
    permission_required = "human_annotations.add_annotationitem"
    paginator_class = LazyPaginator

    def get_queryset(self):
        timezone = self.request.session.get("detected_tz", None)
        filter_params = FilterParams.from_request(self.request)
        queryset = ExperimentSession.objects.filter(team=self.request.team)
        session_filter = ExperimentSessionFilter()
        queryset = session_filter.apply(queryset, filter_params=filter_params, timezone=timezone)
        return (
            queryset.annotate(message_count=Coalesce(Count("chat__messages", distinct=True), 0))
            .select_related("team", "participant__user", "chat", "experiment")
            .order_by("experiment__name")
        )


@login_and_team_required
@permission_required("human_annotations.add_annotationitem")
def annotation_queue_sessions_json(request, team_slug: str, pk: int):
    """Returns filtered session external_ids as JSON for the Alpine session selector."""
    timezone = request.session.get("detected_tz", None)
    filter_params = FilterParams.from_request(request)
    queryset = ExperimentSession.objects.filter(team=request.team)
    session_filter = ExperimentSessionFilter()
    queryset = session_filter.apply(queryset, filter_params=filter_params, timezone=timezone)
    session_keys = list(queryset.values_list("external_id", flat=True))
    return JsonResponse(session_keys, safe=False)
```

**Step 5: Add URL patterns to `apps/human_annotations/urls.py`**

```python
path(
    "queue/<int:pk>/sessions-table/",
    queue_views.AnnotationQueueSessionsTableView.as_view(),
    name="queue_sessions_table",
),
path(
    "queue/<int:pk>/sessions-json/",
    queue_views.annotation_queue_sessions_json,
    name="queue_sessions_json",
),
```

**Step 6: Run tests to verify they pass**

```bash
pytest apps/human_annotations/tests/test_views.py::test_queue_sessions_table_view apps/human_annotations/tests/test_views.py::test_queue_sessions_table_only_shows_team_sessions apps/human_annotations/tests/test_views.py::test_queue_sessions_json_returns_external_ids apps/human_annotations/tests/test_views.py::test_queue_sessions_json_requires_login -v
```

Expected: all PASS

**Step 7: Lint**

```bash
ruff check apps/human_annotations/views/queue_views.py apps/human_annotations/urls.py --fix
ruff format apps/human_annotations/views/queue_views.py apps/human_annotations/urls.py
```

**Step 8: Commit**

```bash
git add apps/human_annotations/views/queue_views.py apps/human_annotations/urls.py apps/human_annotations/tests/test_views.py
git commit -m "feat: add queue sessions table and JSON views"
```

---

### Task 3: Update `AddSessionsToQueue` GET + POST

**Files:**
- Modify: `apps/human_annotations/views/queue_views.py`
- Test: `apps/human_annotations/tests/test_views.py`

**Step 1: Write failing tests**

Add to `apps/human_annotations/tests/test_views.py`:

```python
@pytest.mark.django_db()
def test_add_sessions_get_renders_filter_context(client, team_with_users, queue):
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert "df_filter_columns" in response.context
    assert "df_filter_data_source_url" in response.context


@pytest.mark.django_db()
def test_add_sessions_post_creates_items_from_external_ids(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(2, team=team_with_users)
    session_ids = ",".join(str(s.external_id) for s in sessions)
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"session_ids": session_ids})
    assert response.status_code == 302
    from apps.human_annotations.models import AnnotationItem
    assert AnnotationItem.objects.filter(queue=queue).count() == 2


@pytest.mark.django_db()
def test_add_sessions_post_skips_duplicates(client, team_with_users, queue):
    from apps.human_annotations.models import AnnotationItem
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    existing_session = item.session
    new_session = ExperimentSessionFactory(team=team_with_users)
    session_ids = ",".join([str(existing_session.external_id), str(new_session.external_id)])
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    client.post(url, {"session_ids": session_ids})
    assert AnnotationItem.objects.filter(queue=queue).count() == 2  # 1 old + 1 new


@pytest.mark.django_db()
def test_add_sessions_post_empty_redirects_with_error(client, team_with_users, queue):
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"session_ids": ""})
    assert response.status_code == 302
    from apps.human_annotations.models import AnnotationItem
    assert AnnotationItem.objects.filter(queue=queue).count() == 0


@pytest.mark.django_db()
def test_add_sessions_post_ignores_other_team_sessions(client, team_with_users, queue):
    other_session = ExperimentSessionFactory()  # different team
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    client.post(url, {"session_ids": str(other_session.external_id)})
    from apps.human_annotations.models import AnnotationItem
    assert AnnotationItem.objects.filter(queue=queue).count() == 0
```

**Step 2: Run tests to verify they fail**

```bash
pytest apps/human_annotations/tests/test_views.py::test_add_sessions_get_renders_filter_context apps/human_annotations/tests/test_views.py::test_add_sessions_post_creates_items_from_external_ids -v
```

Expected: FAIL — GET lacks filter context; POST reads `sessions` not `session_ids`.

**Step 3: Update `AddSessionsToQueue.get()` in `queue_views.py`**

Replace the existing `get` method:

```python
def get(self, request, team_slug: str, pk: int):
    queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
    table_url = reverse("human_annotations:queue_sessions_table", args=[team_slug, pk])
    sessions_json_url = reverse("human_annotations:queue_sessions_json", args=[team_slug, pk])
    filter_context = get_filter_context_data(
        request.team,
        columns=ExperimentSessionFilter.columns(request.team),
        filter_class=ExperimentSessionFilter,
        table_url=table_url,
        table_container_id="sessions-table",
        table_type=FilterSet.TableType.SESSIONS,
    )
    return render(
        request,
        "human_annotations/add_items_from_sessions.html",
        {
            "queue": queue,
            "sessions_json_url": sessions_json_url,
            "active_tab": "annotation_queues",
            **filter_context,
        },
    )
```

**Step 4: Update `AddSessionsToQueue.post()` in `queue_views.py`**

Replace the existing `post` method:

```python
def post(self, request, team_slug: str, pk: int):
    queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
    session_ids_raw = request.POST.get("session_ids", "")
    external_ids = [s.strip() for s in session_ids_raw.split(",") if s.strip()]

    if not external_ids:
        messages.error(request, "No sessions selected.")
        return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)

    sessions = list(ExperimentSession.objects.filter(external_id__in=external_ids, team=request.team))
    existing_session_ids = set(
        AnnotationItem.objects.filter(
            queue=queue,
            session__in=sessions,
        ).values_list("session_id", flat=True)
    )

    items_to_create = [
        AnnotationItem(
            queue=queue,
            team=request.team,
            item_type=AnnotationItemType.SESSION,
            session=session,
        )
        for session in sessions
        if session.id not in existing_session_ids
    ]
    created = AnnotationItem.objects.bulk_create(items_to_create, ignore_conflicts=True)
    skipped = len(sessions) - len(created)

    msg = f"Added {len(created)} items to queue."
    if skipped:
        msg += f" Skipped {skipped} duplicates."
    messages.success(request, msg)
    return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)
```

**Step 5: Run tests**

```bash
pytest apps/human_annotations/tests/test_views.py -k "add_sessions" -v
```

Expected: all PASS

**Step 6: Lint**

```bash
ruff check apps/human_annotations/views/queue_views.py --fix
ruff format apps/human_annotations/views/queue_views.py
```

**Step 7: Commit**

```bash
git add apps/human_annotations/views/queue_views.py apps/human_annotations/tests/test_views.py
git commit -m "feat: update AddSessionsToQueue to use filterable session selector"
```

---

### Task 4: Alpine.js component

**Files:**
- Create: `assets/javascript/apps/human_annotations/session-selector.js`

No unit tests for vanilla JS in this project (evaluations JS has none either). Test via template in Task 6.

**Step 1: Create the directory and file**

Create `assets/javascript/apps/human_annotations/session-selector.js`:

```javascript
/**
 * Alpine.js component for selecting sessions to add to an annotation queue.
 *
 * Usage:
 *   x-data="annotationQueueSessionSelector({ sessionIdsFetchUrl: '...' })"
 */
window.annotationQueueSessionSelector = function (options = {}) {
  return {
    selectedSessionIds: new Set(),
    allSessionIds: new Set(),
    sessionIdsFetchUrl: options.sessionIdsFetchUrl || '',
    sessionIdsString: '',
    errorMessages: [],
    sessionIdsIsLoading: false,

    init() {
      window.addEventListener('dataset-mode:table-update', () => this.restoreCheckboxStates());
      window.addEventListener('filter:change', () => this.loadSessionIds());
      this.loadSessionIds();
    },

    async loadSessionIds() {
      if (this.sessionIdsIsLoading) return;
      this.sessionIdsIsLoading = true;
      try {
        const res = await fetch(this.sessionIdsFetchUrl + window.location.search, {
          credentials: 'same-origin',
          headers: {
            'X-CSRFToken': window.SiteJS.app.Cookies.get('csrftoken'),
            'Accept': 'application/json',
          },
        });
        const data = await res.json();
        this.allSessionIds = new Set(data.map(String));
      } catch (_e) {
        this.errorMessages = ['Failed to load sessions. Please refresh the page.'];
      } finally {
        this.sessionIdsIsLoading = false;
      }
    },

    updateSelectedSessions() {
      const allCheckboxes = document.querySelectorAll('tbody .session-checkbox');
      const currentPageIds = Array.from(allCheckboxes).map(cb => cb.value);
      const checkedIds = Array.from(
        document.querySelectorAll('tbody .session-checkbox:checked')
      ).map(cb => cb.value);

      // Remove current page from selected set, then add back only what's checked
      this.selectedSessionIds = new Set(
        [...this.selectedSessionIds].filter(id => !currentPageIds.includes(id))
      );
      checkedIds.forEach(id => this.selectedSessionIds.add(id));

      this.syncHiddenField();
      this.updateHeaderCheckbox();
      this.errorMessages = [];
    },

    toggleSelectedSessions() {
      const header = document.querySelector('thead .session-checkbox');
      if (header && header.checked) {
        this.allSessionIds.forEach(id => this.selectedSessionIds.add(String(id)));
      } else {
        document.querySelectorAll('tbody .session-checkbox').forEach(cb => {
          this.selectedSessionIds.delete(cb.value);
        });
      }
      this.syncHiddenField();
      this.restoreCheckboxStates();
    },

    clearAllSelections() {
      this.selectedSessionIds = new Set();
      this.syncHiddenField();
      document.querySelectorAll('.session-checkbox:checked').forEach(cb => (cb.checked = false));
      this.updateHeaderCheckbox();
    },

    restoreCheckboxStates() {
      document.querySelectorAll('tbody .session-checkbox').forEach(cb => {
        cb.checked = this.selectedSessionIds.has(cb.value);
      });
      this.updateHeaderCheckbox();
    },

    updateHeaderCheckbox() {
      const header = document.querySelector('thead .session-checkbox');
      if (!header) return;
      const pageIds = Array.from(document.querySelectorAll('tbody .session-checkbox')).map(
        cb => cb.value
      );
      header.checked = pageIds.length > 0 && pageIds.every(id => this.selectedSessionIds.has(id));
    },

    syncHiddenField() {
      this.sessionIdsString = Array.from(this.selectedSessionIds).join(',');
    },

    validateAndSubmit(e) {
      this.errorMessages = [];
      if (this.selectedSessionIds.size === 0) {
        e.preventDefault();
        this.errorMessages = ['Please select at least one session.'];
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    },
  };
};
```

**Step 2: Lint**

```bash
npm run lint assets/javascript/apps/human_annotations/session-selector.js
```

Fix any issues, then proceed.

**Step 3: Commit**

```bash
git add assets/javascript/apps/human_annotations/session-selector.js
git commit -m "feat: add annotationQueueSessionSelector Alpine component"
```

---

### Task 5: Webpack entry

**Files:**
- Modify: `webpack.config.js`

**Step 1: Add entry**

In `webpack.config.js`, in the `entry` object of the legacy `config` (the first config block, lines ~9-23), add:

```js
'human_annotations': './assets/javascript/apps/human_annotations/session-selector.js',
```

It should look like:
```js
entry: {
  // ... existing entries ...
  'evaluations': './assets/javascript/apps/evaluations/dataset-mode-selector.js',
  'evaluationTrends': './assets/javascript/apps/evaluations/trend-charts.js',
  'human_annotations': './assets/javascript/apps/human_annotations/session-selector.js',
},
```

**Step 2: Build and verify**

```bash
npm run dev
```

Expected: builds successfully, `static/js/human_annotations-bundle.js` is created.

**Step 3: Commit**

```bash
git add webpack.config.js static/js/human_annotations-bundle.js static/js/human_annotations-bundle.js.map
git commit -m "feat: add human_annotations webpack bundle for session selector"
```

---

### Task 6: Rewrite the template

**Files:**
- Modify: `templates/human_annotations/add_items_from_sessions.html`

**Step 1: Replace the template**

Rewrite `templates/human_annotations/add_items_from_sessions.html` entirely:

```django
{% extends "web/app/app_base.html" %}
{% load i18n static %}

{% block breadcrumbs %}
  <div class="text-sm breadcrumbs" aria-label="breadcrumbs">
    <ul>
      <li><a href="{% url 'human_annotations:queue_home' request.team.slug %}">Annotation Queues</a></li>
      <li><a href="{% url 'human_annotations:queue_detail' request.team.slug queue.pk %}">{{ queue.name }}</a></li>
      <li class="pg-breadcrumb-active" aria-current="page">{% translate "Add Sessions" %}</li>
    </ul>
  </div>
{% endblock breadcrumbs %}

{% block app %}
<div x-data="annotationQueueSessionSelector({ sessionIdsFetchUrl: '{{ sessions_json_url }}' })"
     x-init="init()">

  <div class="flex flex-col gap-4">
    <h2 class="text-xl font-bold">{% blocktranslate with name=queue.name %}Add Sessions to "{{ name }}"{% endblocktranslate %}</h2>

    <!-- Error messages -->
    <div x-show="errorMessages.length" class="alert alert-error mb-4" x-cloak>
      <i class="fa-solid fa-exclamation-triangle"></i>
      <div>
        <template x-for="error in errorMessages">
          <p x-text="error"></p>
        </template>
      </div>
    </div>

    <!-- Filter controls + session count -->
    <div class="flex items-center justify-between flex-wrap gap-2">
      <div>
        {% include "experiments/filters.html" %}
      </div>
      <div class="flex items-center gap-3 text-sm font-medium">
        <div x-show="selectedSessionIds.size > 0">
          <span x-text="selectedSessionIds.size"></span>
          {% translate "session" %}<span x-show="selectedSessionIds.size !== 1">s</span>
          {% translate "of" %} <span x-text="allSessionIds.size"></span> {% translate "selected" %}
          <button type="button" class="btn btn-xs btn-outline ml-2" @click="clearAllSelections()">
            {% translate "Clear" %}
          </button>
        </div>
        <div x-show="selectedSessionIds.size === 0 && allSessionIds.size > 0">
          <span x-text="allSessionIds.size"></span>
          {% translate "session" %}<span x-show="allSessionIds.size !== 1">s</span>
        </div>
      </div>
    </div>

    <!-- Sessions table (HTMX lazy-loaded) -->
    <div id="sessions-table"
         data-url="{% url 'human_annotations:queue_sessions_table' request.team.slug queue.pk %}">
      {% include "table/table_placeholder.html" %}
    </div>

    <!-- Submission form -->
    <form method="post" @submit="validateAndSubmit($event)">
      {% csrf_token %}
      <input type="hidden" name="session_ids" x-model="sessionIdsString">
      <div class="flex gap-2 mt-4">
        <button type="submit" class="btn btn-primary">
          {% translate "Add to Queue" %}
        </button>
        <a href="{% url 'human_annotations:queue_detail' team_slug=request.team.slug pk=queue.pk %}"
           class="btn btn-ghost">
          {% translate "Cancel" %}
        </a>
      </div>
    </form>
  </div>

</div>
{% endblock app %}

{% block page_js %}
  <script src="{% static 'js/human_annotations-bundle.js' %}"></script>
{% endblock page_js %}
```

**Step 2: Lint the template**

```bash
ruff check apps/human_annotations/ --fix  # catches any Python issues from template tags
```

djLint will run via pre-commit. Check for issues:

```bash
pre-commit run djlint --files templates/human_annotations/add_items_from_sessions.html
```

Fix any reported issues.

**Step 3: Smoke-test in browser (optional)**

Start dev server: `inv runserver`

Navigate to any annotation queue detail page → click "Add Sessions" → verify the filter bar and paginated table appear, checkboxes work, count updates, and form submits correctly.

**Step 4: Commit**

```bash
git add templates/human_annotations/add_items_from_sessions.html
git commit -m "feat: rewrite add sessions template with filterable session table"
```

---

### Task 7: Run full test suite

**Step 1: Run all annotation queue tests**

```bash
pytest apps/human_annotations/ -v
```

Expected: all PASS

**Step 2: Run broader smoke test**

```bash
pytest apps/human_annotations/ apps/evaluations/ -v --tb=short
```

Expected: all PASS (verifies no regressions in evaluations)

**Step 3: Type check**

```bash
ty check apps/human_annotations/
```

Fix any issues.

**Step 4: Final commit if any fixes were needed**

```bash
git add -p
git commit -m "fix: address type check issues in annotation queue session selector"
```
