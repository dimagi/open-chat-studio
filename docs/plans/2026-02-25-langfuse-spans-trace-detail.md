# Langfuse Span Tree on Trace Detail Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an HTMX lazy-loaded section to the OCS trace detail page that fetches and displays the Langfuse span/observation tree for the trace, using the Langfuse API credentials linked to the experiment.

**Architecture:** A new `TraceLangufuseSpansView` view handles `GET /a/<team_slug>/traces/<pk>/langfuse-spans/`. It extracts the Langfuse trace ID from the output message's `trace_info` metadata, creates a `FernLangfuse` management API client from the experiment's `TraceProvider` credentials, fetches all observations, builds a tree, and renders a partial template. The trace detail page includes an HTMX placeholder that triggers this request on load.

**Tech Stack:** Django class-based views, `FernLangfuse` (from `langfuse.api.client`), HTMX lazy load (`hx-trigger="load"`), Alpine.js for collapsible spans, DaisyUI components, pytest with `unittest.mock`.

---

### Task 1: Add `get_langfuse_api_client` helper

**Files:**
- Modify: `apps/service_providers/tracing/langfuse.py`

Add this function after the `LangFuseTracer` class (before `ClientManager`). No separate test needed — it is covered by the view tests in Task 2.

**Step 1: Add `get_langfuse_api_client` to `langfuse.py`**

```python
def get_langfuse_api_client(config: dict) -> "FernLangfuse":
    """Create a Langfuse management API client for reading trace data."""
    from langfuse.api.client import FernLangfuse

    return FernLangfuse(
        base_url=config["host"],
        username=config["public_key"],
        password=config["secret_key"],
        timeout=10,
    )
```

Also add to the existing `TYPE_CHECKING` block at the top of the file:

```python
if TYPE_CHECKING:
    from langfuse.api.client import FernLangfuse
    # ... existing imports unchanged
```

**Step 2: Lint**

```bash
ruff check apps/service_providers/tracing/langfuse.py --fix
ruff format apps/service_providers/tracing/langfuse.py
```

**Step 3: Commit**

```bash
git add apps/service_providers/tracing/langfuse.py
git commit -m "feat: add get_langfuse_api_client helper for reading trace data"
```

---

### Task 2: Add `apps/trace/tests/conftest.py`, URL, view, and tests — TDD

**Files:**
- Create: `apps/trace/tests/conftest.py`
- Modify: `apps/trace/urls.py`
- Modify: `apps/trace/views.py`
- Create: `apps/trace/tests/test_langfuse_spans_view.py`

**Step 1: Create `apps/trace/tests/conftest.py`**

```python
import pytest
from django.test import Client

from apps.utils.factories.team import MembershipFactory, TeamFactory, get_test_user_groups
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def user():
    return UserFactory()


@pytest.fixture()
def team(user):
    team = TeamFactory()
    MembershipFactory(team=team, user=user, groups=get_test_user_groups)
    return team


@pytest.fixture()
def client():
    return Client()
```

**Step 2: Add the URL**

Add to `apps/trace/urls.py`:

```python
path("<int:pk>/langfuse-spans/", views.TraceLangufuseSpansView.as_view(), name="trace_langfuse_spans"),
```

Full file after change:

```python
from django.urls import path

from . import views

app_name = "trace"
urlpatterns = [
    path("home/", views.TracesHome.as_view(), name="home"),
    path("table/", views.TraceTableView.as_view(), name="table"),
    path("<int:pk>/", views.TraceDetailView.as_view(), name="trace_detail"),
    path("<int:pk>/langfuse-spans/", views.TraceLangufuseSpansView.as_view(), name="trace_langfuse_spans"),
]
```

**Step 3: Write the failing tests**

```python
# apps/trace/tests/test_langfuse_spans_view.py
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from apps.service_providers.models import TraceProviderType
from apps.trace.models import TraceStatus
from apps.trace.views import TraceLangufuseSpansView
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentFactory
from apps.utils.factories.service_provider_factories import TraceProviderFactory
from apps.utils.factories.traces import TraceFactory


LANGFUSE_TRACE_ID = "lf-trace-abc123"
LANGFUSE_TRACE_URL = "https://cloud.langfuse.com/project/xxx/traces/lf-trace-abc123"


def _make_observation(obs_id, name, level="DEFAULT", parent_id=None):
    obs = MagicMock()
    obs.id = obs_id
    obs.name = name
    obs.level = level
    obs.type = "SPAN"
    obs.status_message = None
    obs.input = {"prompt": "test"}
    obs.output = {"response": "test"}
    obs.latency = 0.5
    obs.start_time = None
    obs.parent_observation_id = parent_id
    return obs


class TestBuildChildMap:
    """Unit tests for tree-building logic — no DB needed."""

    def test_separates_root_and_child_observations(self):
        view = TraceLangufuseSpansView()
        root = _make_observation("obs-1", "Root")
        child = _make_observation("obs-2", "Child", parent_id="obs-1")
        result = view._build_child_map([root, child])
        assert result == {"obs-1": [child]}

    def test_multiple_children_under_same_parent(self):
        view = TraceLangufuseSpansView()
        root = _make_observation("obs-1", "Root")
        child_a = _make_observation("obs-2", "Child A", parent_id="obs-1")
        child_b = _make_observation("obs-3", "Child B", parent_id="obs-1")
        result = view._build_child_map([root, child_a, child_b])
        assert result == {"obs-1": [child_a, child_b]}

    def test_returns_plain_dict_not_defaultdict(self):
        from collections import defaultdict

        view = TraceLangufuseSpansView()
        result = view._build_child_map([])
        assert not isinstance(result, defaultdict)
        assert isinstance(result, dict)

    def test_observation_with_no_parent_not_in_map(self):
        view = TraceLangufuseSpansView()
        root = _make_observation("obs-1", "Root")
        result = view._build_child_map([root])
        assert result == {}


@pytest.mark.django_db()
class TestTraceLangufuseSpansView:
    @pytest.fixture()
    def trace_provider(self, team):
        return TraceProviderFactory(
            team=team,
            type=TraceProviderType.langfuse,
            config={"public_key": "pk-test", "secret_key": "sk-test", "host": "https://cloud.langfuse.com"},
        )

    @pytest.fixture()
    def experiment(self, team, trace_provider):
        return ExperimentFactory(team=team, trace_provider=trace_provider)

    @pytest.fixture()
    def output_message(self):
        return ChatMessageFactory(
            metadata={
                "trace_info": [
                    {
                        "trace_id": LANGFUSE_TRACE_ID,
                        "trace_url": LANGFUSE_TRACE_URL,
                        "trace_provider": "langfuse",
                    }
                ]
            }
        )

    @pytest.fixture()
    def trace(self, team, experiment, output_message):
        return TraceFactory(team=team, experiment=experiment, output_message=output_message, status=TraceStatus.SUCCESS)

    def _url(self, team, trace):
        return reverse("trace:trace_langfuse_spans", args=[team.slug, trace.pk])

    def test_no_langfuse_provider_returns_not_available(self, client, team, user):
        """Experiment has no trace_provider: show 'not available' note."""
        experiment = ExperimentFactory(team=team, trace_provider=None)
        output_message = ChatMessageFactory(metadata={})
        trace = TraceFactory(team=team, experiment=experiment, output_message=output_message)
        client.force_login(user)
        response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_not_available" in response.content

    def test_no_langfuse_trace_info_returns_not_available(self, client, team, user, trace_provider):
        """Output message has no Langfuse trace_info: show 'not available' note."""
        experiment = ExperimentFactory(team=team, trace_provider=trace_provider)
        output_message = ChatMessageFactory(metadata={"trace_info": [{"trace_provider": "ocs", "trace_id": "123"}]})
        trace = TraceFactory(team=team, experiment=experiment, output_message=output_message)
        client.force_login(user)
        response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_not_available" in response.content

    def test_no_output_message_returns_not_available(self, client, team, user, trace_provider):
        """Trace has no output_message: show 'not available' note."""
        experiment = ExperimentFactory(team=team, trace_provider=trace_provider)
        trace = TraceFactory(team=team, experiment=experiment, output_message=None)
        client.force_login(user)
        response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_not_available" in response.content

    def test_none_trace_id_in_trace_info_returns_not_available(self, client, team, user, trace_provider):
        """trace_info has a Langfuse entry but trace_id is None: show 'not available' note."""
        experiment = ExperimentFactory(team=team, trace_provider=trace_provider)
        output_message = ChatMessageFactory(
            metadata={"trace_info": [{"trace_provider": "langfuse", "trace_id": None, "trace_url": LANGFUSE_TRACE_URL}]}
        )
        trace = TraceFactory(team=team, experiment=experiment, output_message=output_message)
        client.force_login(user)
        response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_not_available" in response.content

    def test_langfuse_api_error_returns_error_partial(self, client, team, user, trace):
        """Langfuse API call fails: show error partial with fallback link."""
        client.force_login(user)
        with patch("apps.trace.views.get_langfuse_api_client") as mock_client_factory:
            mock_api = MagicMock()
            mock_api.trace.get.side_effect = Exception("API unreachable")
            mock_client_factory.return_value = mock_api
            response = client.get(self._url(team, trace))
        assert response.status_code == 200
        assert b"langfuse_error" in response.content
        assert LANGFUSE_TRACE_URL.encode() in response.content

    def test_successful_fetch_renders_observations(self, client, team, user, trace):
        """Successful Langfuse fetch: render span tree with observation names."""
        root_obs = _make_observation("obs-1", "Pipeline Run")
        child_obs = _make_observation("obs-2", "LLM Call", parent_id="obs-1")
        mock_trace_data = MagicMock()
        mock_trace_data.observations = [root_obs, child_obs]

        client.force_login(user)
        with patch("apps.trace.views.get_langfuse_api_client") as mock_client_factory:
            mock_api = MagicMock()
            mock_api.trace.get.return_value = mock_trace_data
            mock_client_factory.return_value = mock_api
            response = client.get(self._url(team, trace))

        assert response.status_code == 200
        assert b"Pipeline Run" in response.content
        assert b"LLM Call" in response.content
        assert LANGFUSE_TRACE_URL.encode() in response.content
        mock_api.trace.get.assert_called_once_with(LANGFUSE_TRACE_ID)
```

**Step 4: Run tests to verify they fail**

```bash
pytest apps/trace/tests/test_langfuse_spans_view.py -v
```
Expected: import errors and fixture errors — `TraceLangufuseSpansView` does not exist yet.

**Step 5: Implement `TraceLangufuseSpansView` in `apps/trace/views.py`**

Add at the top of `apps/trace/views.py` (with existing imports):

```python
import logging
from collections import defaultdict

from apps.service_providers.tracing.langfuse import get_langfuse_api_client
```

Add `logger` at module level (after imports):

```python
logger = logging.getLogger(__name__)
```

Add the view at the bottom of `apps/trace/views.py`:

```python
class TraceLangufuseSpansView(LoginAndTeamRequiredMixin, DetailView, PermissionRequiredMixin):
    model = Trace
    template_name = "trace/partials/langfuse_spans.html"
    permission_required = "trace.view_trace"

    def get_queryset(self):
        return Trace.objects.select_related(
            "experiment__trace_provider", "output_message"
        ).filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        trace = self.object
        langfuse_trace_id, langfuse_trace_url = self._get_langfuse_info(trace)
        context["langfuse_trace_url"] = langfuse_trace_url

        if not langfuse_trace_id or not trace.experiment.trace_provider:
            context["langfuse_available"] = False
            context["langfuse_error"] = False
            return context

        try:
            api_client = get_langfuse_api_client(trace.experiment.trace_provider.config)
            langfuse_trace = api_client.trace.get(langfuse_trace_id)
            observations = langfuse_trace.observations or []
            context["langfuse_available"] = True
            context["langfuse_error"] = False
            context["root_observations"] = [o for o in observations if not o.parent_observation_id]
            context["child_observations_map"] = self._build_child_map(observations)
        except Exception:
            logger.exception("Error fetching Langfuse trace %s", langfuse_trace_id)
            context["langfuse_available"] = False
            context["langfuse_error"] = True

        return context

    def _get_langfuse_info(self, trace) -> tuple[str | None, str | None]:
        if not trace.output_message:
            return None, None
        for info in trace.output_message.trace_info:
            if info.get("trace_provider") == "langfuse":
                return info.get("trace_id"), info.get("trace_url")
        return None, None

    def _build_child_map(self, observations) -> dict:
        child_map: dict = defaultdict(list)
        for obs in observations:
            if obs.parent_observation_id:
                child_map[obs.parent_observation_id].append(obs)
        return dict(child_map)
```

**Step 6: Run tests to verify they pass**

```bash
pytest apps/trace/tests/test_langfuse_spans_view.py -v
```
Expected: all tests PASS (4 unit tests + 6 DB tests = 10 total).

**Step 7: Lint**

```bash
ruff check apps/trace/views.py apps/trace/urls.py apps/trace/tests/ --fix
ruff format apps/trace/views.py apps/trace/urls.py apps/trace/tests/
```

**Step 8: Commit**

```bash
git add apps/trace/tests/conftest.py apps/trace/tests/test_langfuse_spans_view.py apps/trace/views.py apps/trace/urls.py
git commit -m "feat: add TraceLangufuseSpansView for HTMX lazy-loaded Langfuse spans"
```

---

### Task 3: Create the partial templates

**Files:**
- Create: `templates/trace/partials/langfuse_spans.html`
- Create: `templates/trace/partials/langfuse_observation.html`

**Step 1: Create the directory**

```bash
mkdir -p templates/trace/partials
```

**Step 2: Create `templates/trace/partials/langfuse_spans.html`**

```html
{% load i18n json_tags %}

{% if not langfuse_available and not langfuse_error %}
{# No Langfuse provider or no trace info — show a subtle note #}
<div id="langfuse-spans-section" class="langfuse_not_available mt-6">
    <p class="text-xs text-base-content/40 text-center">No Langfuse trace available for this trace.</p>
</div>

{% elif not langfuse_available and langfuse_error %}
{# API call failed — show error with optional fallback link #}
<div id="langfuse-spans-section" class="langfuse_error mt-6">
    <div class="card bg-base-100 shadow-md">
        <div class="card-body">
            <div role="alert" class="alert alert-warning">
                <i class="fa-solid fa-triangle-exclamation"></i>
                <div>
                    <p>Could not load Langfuse trace data.</p>
                    {% if langfuse_trace_url %}
                        <a href="{{ langfuse_trace_url }}" target="_blank" rel="noopener noreferrer" class="link">
                            View directly on Langfuse <i class="fa-solid fa-arrow-up-right-from-square text-xs"></i>
                        </a>
                    {% endif %}
                </div>
            </div>
        </div>
    </div>
</div>

{% else %}
{# Successful fetch — show span tree #}
<div id="langfuse-spans-section" class="mt-6">
    <div class="card bg-base-100 shadow-md">
        <div class="card-header border-b border-base-200 px-6 py-4">
            <div class="flex justify-between items-center">
                <h3 class="text-lg font-semibold text-base-content">Langfuse Trace</h3>
                <a href="{{ langfuse_trace_url }}" target="_blank" rel="noopener noreferrer"
                   class="btn btn-sm btn-outline gap-1" title="View full trace on Langfuse">
                    {% include "svg/langfuse.svg" %}
                    <span>View in Langfuse</span>
                </a>
            </div>
        </div>
        <div class="card-body p-4">
            {% if root_observations %}
                <div class="space-y-2">
                    {% for obs in root_observations %}
                        {% include "trace/partials/langfuse_observation.html" with observation=obs depth=0 %}
                    {% endfor %}
                </div>
            {% else %}
                <p class="text-base-content/60 text-sm">No observations recorded for this trace.</p>
            {% endif %}
        </div>
    </div>
</div>
{% endif %}
```

**Step 3: Create `templates/trace/partials/langfuse_observation.html`**

This recursive sub-template renders a single observation and its children. `{% include %}` without `only` inherits the parent context (including `child_observations_map`), so the recursive call works without explicitly passing it.

The `get_item` template filter is registered globally in this project, so `child_observations_map|get_item:observation.id` is safe to use.

```html
{% load json_tags %}
{% with children=child_observations_map|get_item:observation.id %}
<div x-data="{ open: false }" class="border border-base-200 rounded-lg">
    {# Header row — always visible #}
    <div class="flex items-center gap-3 px-4 py-2 cursor-pointer hover:bg-base-50"
         @click="open = !open">
        <button class="btn btn-xs btn-ghost btn-circle" :aria-label="open ? 'Collapse' : 'Expand'">
            <i class="fa-solid fa-chevron-right transition-transform" :class="open ? 'rotate-90' : ''"></i>
        </button>

        {# Status badge #}
        {% if observation.level == "ERROR" %}
            <span class="badge badge-error badge-sm">ERROR</span>
        {% elif observation.level == "WARNING" %}
            <span class="badge badge-warning badge-sm">WARN</span>
        {% else %}
            <span class="badge badge-success badge-sm">OK</span>
        {% endif %}

        <span class="text-sm font-medium flex-1">{{ observation.name }}</span>

        {% if observation.latency %}
            <span class="text-xs text-base-content/50">{{ observation.latency|floatformat:3 }}s</span>
        {% endif %}
    </div>

    {# Expandable body #}
    <div x-show="open" class="border-t border-base-200 px-4 py-3 space-y-3">
        {% if observation.status_message %}
            <div class="text-sm text-error">{{ observation.status_message }}</div>
        {% endif %}

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {% if observation.input %}
                <div>
                    <div class="text-xs font-medium text-base-content/60 mb-1">Input</div>
                    <pre class="text-xs bg-base-200 rounded p-2 overflow-x-auto whitespace-pre-wrap">{{ observation.input|to_json }}</pre>
                </div>
            {% endif %}
            {% if observation.output %}
                <div>
                    <div class="text-xs font-medium text-base-content/60 mb-1">Output</div>
                    <pre class="text-xs bg-base-200 rounded p-2 overflow-x-auto whitespace-pre-wrap">{{ observation.output|to_json }}</pre>
                </div>
            {% endif %}
        </div>

        {# Child observations #}
        {% if children %}
            <div class="space-y-2 pl-4 border-l-2 border-base-200">
                {% for child in children %}
                    {% include "trace/partials/langfuse_observation.html" with observation=child depth=depth|add:1 %}
                {% endfor %}
            </div>
        {% endif %}
    </div>
</div>
{% endwith %}
```

Note: `x-collapse` (Alpine Collapse plugin) is omitted intentionally — use `x-show` only, as the Collapse plugin may not be present. Verify with:

```bash
grep -r "x-collapse\|alpinejs/collapse" templates/ --include="*.html" | head -5
```

If the plugin is available, replace `x-show="open"` with `x-show="open" x-collapse` for animation.

**Step 4: Lint templates**

```bash
python -m djlint templates/trace/partials/ --check
```
Fix any issues reported.

**Step 5: Commit**

```bash
git add templates/trace/partials/
git commit -m "feat: add Langfuse observation tree partial templates"
```

---

### Task 4: Add HTMX placeholder to trace detail page

**Files:**
- Modify: `templates/trace/trace_detail.html`

**Step 1: Add the HTMX placeholder after line 79**

In `templates/trace/trace_detail.html`, after `{% include "trace/inputs_outputs.html" %}`, add:

```html
        {# Langfuse spans — lazy loaded via HTMX #}
        <div hx-get="{% url 'trace:trace_langfuse_spans' request.team.slug trace.pk %}"
             hx-trigger="load"
             hx-swap="outerHTML">
            <div class="mt-6 flex justify-center py-8">
                <span class="loading loading-spinner loading-md text-base-content/40"></span>
            </div>
        </div>
```

The full updated block:

```html
    <div class="border-t border-base-200 mt-2 pt-4">
        {% if trace.error %}
        <!-- Error Message -->
        <div role="alert" class="alert alert-error mb-6">
            <i class="fa-solid fa-circle-exclamation"></i>
            <div class="text-sm">{{ trace.error|render_markdown }}</div>
        </div>
        {% endif %}

        {% include "trace/inputs_outputs.html" %}

        {# Langfuse spans — lazy loaded via HTMX #}
        <div hx-get="{% url 'trace:trace_langfuse_spans' request.team.slug trace.pk %}"
             hx-trigger="load"
             hx-swap="outerHTML">
            <div class="mt-6 flex justify-center py-8">
                <span class="loading loading-spinner loading-md text-base-content/40"></span>
            </div>
        </div>
    </div>
```

**Step 2: Run full trace test suite**

```bash
pytest apps/trace/ -v
```
Expected: all tests PASS.

**Step 3: Lint the template**

```bash
python -m djlint templates/trace/trace_detail.html --check
```

**Step 4: Commit**

```bash
git add templates/trace/trace_detail.html
git commit -m "feat: add HTMX lazy-load placeholder for Langfuse spans on trace detail"
```

---

### Task 5: Final verification pass

**Step 1: Run full test suite for all touched apps**

```bash
pytest apps/trace/ apps/service_providers/tracing/ -v
```
Expected: all tests PASS.

**Step 2: Typecheck**

```bash
ty check apps/trace/views.py apps/service_providers/tracing/langfuse.py
```

**Step 3: Lint all touched files**

```bash
ruff check apps/trace/ apps/service_providers/tracing/langfuse.py --fix
ruff format apps/trace/ apps/service_providers/tracing/langfuse.py
```

**Step 4: Commit any lint fixes if needed**

```bash
git add -p
git commit -m "chore: lint fixes for Langfuse spans feature"
```
