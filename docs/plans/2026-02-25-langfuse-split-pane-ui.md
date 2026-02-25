# Langfuse Trace UI — Split Pane Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current vertically-expanding span tree with a fixed-height split-pane UI — left tree navigator and right detail pane — eliminating long-page scrolling and providing instant orientation.

**Architecture:** Add `_flatten_observations` and `_get_auto_selected_span_id` methods to `TraceLangufuseSpansView`, pass a flat depth-annotated list to the template, and rewrite `langfuse_spans.html` with an Alpine.js split-pane. The recursive `langfuse_observation.html` is replaced by a flat loop.

**Tech Stack:** Django templates, Alpine.js, DaisyUI/Tailwind CSS, existing `readable_value` and `highlight_json` template filters.

---

### Task 1: Add `_flatten_observations` to the view

**Files:**
- Modify: `apps/trace/views.py`
- Test: `apps/trace/tests/test_langfuse_spans_view.py`

**Step 1: Write the failing tests**

Add to `TestBuildChildMap` class in `apps/trace/tests/test_langfuse_spans_view.py`:

```python
def test_flatten_observations_depth_first_with_depths(self):
    view = TraceLangufuseSpansView()
    root = _make_observation("obs-1", "Root")
    child_a = _make_observation("obs-2", "Child A", parent_id="obs-1")
    child_b = _make_observation("obs-3", "Child B", parent_id="obs-1")
    grandchild = _make_observation("obs-4", "Grandchild", parent_id="obs-2")
    child_map = {"obs-1": [child_a, child_b], "obs-2": [grandchild]}

    result = view._flatten_observations([root], child_map)

    assert [(item["observation"].name, item["depth"]) for item in result] == [
        ("Root", 0),
        ("Child A", 1),
        ("Grandchild", 2),
        ("Child B", 1),
    ]

def test_flatten_observations_empty(self):
    view = TraceLangufuseSpansView()
    result = view._flatten_observations([], {})
    assert result == []

def test_flatten_observations_single_span(self):
    view = TraceLangufuseSpansView()
    root = _make_observation("obs-1", "Root")
    result = view._flatten_observations([root], {})
    assert result == [{"observation": root, "depth": 0}]
```

**Step 2: Run tests to confirm they fail**

```bash
uv run pytest apps/trace/tests/test_langfuse_spans_view.py::TestBuildChildMap -v
```
Expected: FAIL with `AttributeError: '_flatten_observations'`

**Step 3: Implement the method**

Add to `TraceLangufuseSpansView` in `apps/trace/views.py` (after `_build_child_map`):

```python
def _flatten_observations(self, root_observations, child_map) -> list:
    """Return depth-first ordered flat list of {"observation": obs, "depth": int} dicts."""
    result = []

    def _walk(obs, depth):
        result.append({"observation": obs, "depth": depth})
        for child in child_map.get(obs.id, []):
            _walk(child, depth + 1)

    for root in root_observations:
        _walk(root, 0)
    return result
```

**Step 4: Run tests to confirm they pass**

```bash
uv run pytest apps/trace/tests/test_langfuse_spans_view.py::TestBuildChildMap -v
```
Expected: All PASS

**Step 5: Commit**

```bash
git add apps/trace/views.py apps/trace/tests/test_langfuse_spans_view.py
git commit -m "feat: add _flatten_observations to TraceLangufuseSpansView"
```

---

### Task 2: Add `_get_auto_selected_span_id` + wire both into context

**Files:**
- Modify: `apps/trace/views.py`
- Test: `apps/trace/tests/test_langfuse_spans_view.py`

**Step 1: Write the failing tests**

Add a new test class to `apps/trace/tests/test_langfuse_spans_view.py`:

```python
class TestAutoSelectedSpanId:
    def test_selects_first_error_span(self):
        view = TraceLangufuseSpansView()
        ok_obs = _make_observation("obs-1", "Root", level="DEFAULT")
        err_obs = _make_observation("obs-2", "Failure", level="ERROR")
        flattened = [
            {"observation": ok_obs, "depth": 0},
            {"observation": err_obs, "depth": 1},
        ]
        assert view._get_auto_selected_span_id(flattened) == "obs-2"

    def test_falls_back_to_first_span_when_no_errors(self):
        view = TraceLangufuseSpansView()
        obs = _make_observation("obs-1", "Root", level="DEFAULT")
        flattened = [{"observation": obs, "depth": 0}]
        assert view._get_auto_selected_span_id(flattened) == "obs-1"

    def test_returns_none_for_empty_list(self):
        view = TraceLangufuseSpansView()
        assert view._get_auto_selected_span_id([]) is None
```

Also update `test_successful_fetch_renders_observations` in `TestTraceLangufuseSpansView` — add these two assertions at the end:

```python
assert "flattened_observations" in response.context
assert response.context["auto_selected_span_id"] == "obs-1"  # no errors, first span
```

**Step 2: Run tests to confirm they fail**

```bash
uv run pytest apps/trace/tests/test_langfuse_spans_view.py::TestAutoSelectedSpanId -v
```
Expected: FAIL with `AttributeError`

**Step 3: Implement the method**

Add to `TraceLangufuseSpansView` in `apps/trace/views.py`:

```python
def _get_auto_selected_span_id(self, flattened_observations) -> str | None:
    """Return the first ERROR span id; fall back to the first span."""
    for item in flattened_observations:
        if item["observation"].level == "ERROR":
            return item["observation"].id
    return flattened_observations[0]["observation"].id if flattened_observations else None
```

**Step 4: Wire both into `get_context_data`**

In `get_context_data`, replace the two lines inside the `try` block:
```python
context["root_observations"] = [o for o in observations if not o.parent_observation_id]
context["child_observations_map"] = self._build_child_map(observations)
```
with:
```python
root_observations = [o for o in observations if not o.parent_observation_id]
child_map = self._build_child_map(observations)
flattened = self._flatten_observations(root_observations, child_map)
context["flattened_observations"] = flattened
context["auto_selected_span_id"] = self._get_auto_selected_span_id(flattened)
```

**Step 5: Run all tests to confirm they pass**

```bash
uv run pytest apps/trace/tests/test_langfuse_spans_view.py -v
```
Expected: All PASS

**Step 6: Lint**

```bash
uv run ruff check apps/trace/views.py --fix && uv run ruff format apps/trace/views.py
```

**Step 7: Commit**

```bash
git add apps/trace/views.py apps/trace/tests/test_langfuse_spans_view.py
git commit -m "feat: add flattened observations and auto-select span to context"
```

---

### Task 3: Rewrite templates — split-pane layout

**Files:**
- Modify: `templates/trace/partials/langfuse_spans.html`
- Delete: `templates/trace/partials/langfuse_observation.html`

**Step 1: Replace `langfuse_spans.html` entirely**

The error/unavailable states at the top are unchanged. Only the success branch (`{% else %}`) gets a new split-pane card body.

Replace the full content of `templates/trace/partials/langfuse_spans.html` with:

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
              <a href="{{ langfuse_trace_url }}"
                 target="_blank"
                 rel="noopener noreferrer"
                 class="link">
                View directly on Langfuse <i class="fa-solid fa-arrow-up-right-from-square text-xs"></i>
              </a>
            {% endif %}
          </div>
        </div>
      </div>
    </div>
  </div>
{% else %}
  {# Successful fetch — split-pane UI #}
  <div id="langfuse-spans-section" class="mt-6">
    <div class="card bg-base-100 shadow-md"
         x-data="{ selectedSpanId: '{{ auto_selected_span_id|default:'' }}' }">

      {# Card header #}
      <div class="card-header border-b border-base-200 px-6 py-4">
        <div class="flex justify-between items-center">
          <h3 class="text-lg font-semibold text-base-content">Langfuse Trace</h3>
          {% if langfuse_trace_url %}
            <a href="{{ langfuse_trace_url }}"
               target="_blank"
               rel="noopener noreferrer"
               class="btn btn-sm btn-outline gap-1"
               title="View full trace on Langfuse">
              {% include "svg/langfuse.svg" %}
              <span>View in Langfuse</span>
            </a>
          {% endif %}
        </div>
      </div>

      {% if flattened_observations %}
        {# Split pane #}
        <div class="flex min-h-96 max-h-[600px] overflow-hidden">

          {# Left pane: tree navigator #}
          <div class="w-1/3 border-r border-base-200 overflow-y-auto py-2 flex-shrink-0">
            {% for item in flattened_observations %}
              <div class="flex items-center gap-2 pr-3 py-1.5 cursor-pointer rounded-lg mx-1 text-sm hover:bg-base-200 transition-colors"
                   style="padding-left: calc(0.75rem + {{ item.depth }} * 1rem)"
                   :class="selectedSpanId === '{{ item.observation.id }}' ? 'bg-primary/10 text-primary font-medium' : ''"
                   @click="selectedSpanId = '{{ item.observation.id }}'">
                {# Status dot #}
                {% if item.observation.level == "ERROR" %}
                  <span class="w-2 h-2 rounded-full bg-error flex-shrink-0"></span>
                {% elif item.observation.level == "WARNING" %}
                  <span class="w-2 h-2 rounded-full bg-warning flex-shrink-0"></span>
                {% else %}
                  <span class="w-2 h-2 rounded-full bg-success/60 flex-shrink-0"></span>
                {% endif %}
                <span class="truncate flex-1">{{ item.observation.name }}</span>
                {% if item.observation.latency %}
                  <span class="text-xs text-base-content/40 flex-shrink-0 tabular-nums">{{ item.observation.latency|floatformat:2 }}s</span>
                {% endif %}
              </div>
            {% endfor %}
          </div>

          {# Right pane: span detail #}
          <div class="flex-1 overflow-y-auto">

            {# Empty state — briefly visible before auto-select kicks in #}
            <div x-show="!selectedSpanId"
                 class="flex items-center justify-center h-full text-base-content/40 text-sm">
              Select a span to view details
            </div>

            {% for item in flattened_observations %}
              <div x-show="selectedSpanId === '{{ item.observation.id }}'"
                   x-cloak
                   class="p-4 space-y-4">

                {# Detail header #}
                <div class="flex items-center gap-2 flex-wrap">
                  <h4 class="text-base font-semibold">{{ item.observation.name }}</h4>
                  {% if item.observation.level == "ERROR" %}
                    <span class="badge badge-error badge-sm">ERROR</span>
                  {% elif item.observation.level == "WARNING" %}
                    <span class="badge badge-warning badge-sm">WARN</span>
                  {% else %}
                    <span class="badge badge-success badge-sm">OK</span>
                  {% endif %}
                  {% if item.observation.latency %}
                    <span class="text-xs text-base-content/50 tabular-nums">{{ item.observation.latency|floatformat:3 }}s</span>
                  {% endif %}
                  {% if item.observation.type %}
                    <span class="badge badge-outline badge-xs">{{ item.observation.type }}</span>
                  {% endif %}
                </div>

                {# Status message — only shown for ERROR #}
                {% if item.observation.status_message %}
                  <div class="text-sm text-error bg-error/10 rounded-lg p-3">{{ item.observation.status_message }}</div>
                {% endif %}

                {# Input #}
                {% if item.observation.input %}
                  {% with readable=item.observation.input|readable_value %}
                    <div x-data="{ showRaw: false }">
                      <div class="flex items-center gap-2 mb-1">
                        <span class="text-xs font-medium text-base-content/60 uppercase tracking-wide">Input</span>
                        {% if readable %}
                          <button type="button"
                                  class="btn btn-xs btn-ghost py-0 h-auto"
                                  @click="showRaw = !showRaw"
                                  x-text="showRaw ? 'Show readable' : 'Show raw JSON'"></button>
                        {% endif %}
                      </div>
                      {% if readable %}
                        <div x-show="!showRaw">
                          <pre class="text-xs bg-base-200 rounded-lg p-3 whitespace-pre-wrap overflow-x-auto">{{ readable }}</pre>
                        </div>
                        <div x-show="showRaw" x-cloak>
                          <pre class="highlight-json text-xs bg-base-200 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{{ item.observation.input|highlight_json }}</pre>
                        </div>
                      {% else %}
                        <pre class="highlight-json text-xs bg-base-200 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{{ item.observation.input|highlight_json }}</pre>
                      {% endif %}
                    </div>
                  {% endwith %}
                {% endif %}

                {# Output #}
                {% if item.observation.output %}
                  {% with readable=item.observation.output|readable_value %}
                    <div x-data="{ showRaw: false }">
                      <div class="flex items-center gap-2 mb-1">
                        <span class="text-xs font-medium text-base-content/60 uppercase tracking-wide">Output</span>
                        {% if readable %}
                          <button type="button"
                                  class="btn btn-xs btn-ghost py-0 h-auto"
                                  @click="showRaw = !showRaw"
                                  x-text="showRaw ? 'Show readable' : 'Show raw JSON'"></button>
                        {% endif %}
                      </div>
                      {% if readable %}
                        <div x-show="!showRaw">
                          <pre class="text-xs bg-base-200 rounded-lg p-3 whitespace-pre-wrap overflow-x-auto">{{ readable }}</pre>
                        </div>
                        <div x-show="showRaw" x-cloak>
                          <pre class="highlight-json text-xs bg-base-200 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{{ item.observation.output|highlight_json }}</pre>
                        </div>
                      {% else %}
                        <pre class="highlight-json text-xs bg-base-200 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{{ item.observation.output|highlight_json }}</pre>
                      {% endif %}
                    </div>
                  {% endwith %}
                {% endif %}

              </div>
            {% endfor %}
          </div>

        </div>
      {% else %}
        <div class="card-body">
          <p class="text-base-content/60 text-sm">No observations recorded for this trace.</p>
        </div>
      {% endif %}

    </div>
  </div>
{% endif %}
```

**Note on `x-cloak`:** This directive hides elements until Alpine initialises, preventing a flash of all spans. It requires the CSS rule `[x-cloak] { display: none !important; }` to be present in the page stylesheet. Check `templates/base.html` or `assets/` for an existing definition; if absent, add it to the `<style>` block in `templates/trace/trace_detail.html`.

**Step 2: Delete the old recursive observation template**

```bash
git rm templates/trace/partials/langfuse_observation.html
```

**Step 3: Lint the template**

```bash
uv run ruff check apps/ --fix
```

**Step 4: Run the full test suite**

```bash
uv run pytest apps/trace/tests/test_langfuse_spans_view.py -v
```
Expected: All PASS (the HTML response tests check for observation names, which are still rendered in the new template)

**Step 5: Commit**

```bash
git add templates/trace/partials/langfuse_spans.html
git commit -m "feat: replace span tree with split-pane UI (tree navigator + detail pane)"
```
