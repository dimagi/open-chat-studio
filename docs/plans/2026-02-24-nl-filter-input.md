# Natural Language Filter Input Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a natural language input to the top of the filter panel that calls the existing `FilterAgent` backend and populates filter rows from the response.

**Architecture:** Extend the existing `filterComponent` Alpine.js object in `templates/experiments/filters.html` with four new state properties (`nlQuery`, `nlLoading`, `nlError`, `filterSlug`) and one new method (`generateFiltersFromNL`). One small backend change: remove `@csrf_exempt` from the `run_agent` view. Update existing tests and add two new ones.

**Tech Stack:** Alpine.js, Fetch API, Django template tags, DaisyUI/Tailwind CSS

---

### Task 0: Remove `@csrf_exempt` from the `run_agent` view + update tests

**Files:**
- Modify: `apps/help/views.py`
- Modify: `apps/help/tests/test_help.py`

**Step 1: Remove `@csrf_exempt` from `views.py`**

In `apps/help/views.py`, remove the `@csrf_exempt` decorator and its import:

```python
# Before
from django.views.decorators.csrf import csrf_exempt

@require_POST
@login_and_team_required
@csrf_exempt
def run_agent(request, team_slug: str, agent_name: str):
```

```python
# After
@require_POST
@login_and_team_required
def run_agent(request, team_slug: str, agent_name: str):
```

Remove the `from django.views.decorators.csrf import csrf_exempt` import line entirely if it is not used elsewhere in the file.

**Step 2: Update `TestRunAgentView._make_request` comment in `test_help.py`**

The `__wrapped__.__wrapped__` call chain resolves to the inner function with two decorators just as it did with three. Only the comment needs updating:

```python
# Before
# Bypass @require_POST (already POST), @login_and_team_required, @csrf_exempt
# by calling the innermost function via __wrapped__
inner = run_agent.__wrapped__.__wrapped__

# After
# Bypass @require_POST (already POST) and @login_and_team_required
# by calling the innermost function via __wrapped__
inner = run_agent.__wrapped__.__wrapped__
```

**Step 3: Add `test_successful_filter_agent_call` to `TestRunAgentView`**

Add a new test after `test_successful_agent_call` that exercises the `filter` agent path and verifies the response shape:

```python
@mock.patch("apps.help.agents.filter.build_system_agent")
def test_successful_filter_agent_call(self, mock_build):
    import apps.experiments.filters  # noqa: F401 — trigger filter registration
    from apps.help.agents.filter import FilterOutput
    from apps.web.dynamic_filters.datastructures import ColumnFilterData

    stub_output = FilterOutput(
        filters=[ColumnFilterData(column="state", operator="equals", value="setup")]
    )
    mock_agent = mock.Mock()
    mock_agent.invoke.return_value = {"structured_response": stub_output}
    mock_build.return_value = mock_agent

    response = self._make_request("filter", {"query": "active sessions", "filter_slug": "session"})

    assert response.status_code == 200
    data = json.loads(response.content)
    assert "response" in data
    assert "filters" in data["response"]
    assert data["response"]["filters"][0]["column"] == "state"
```

**Step 4: Run tests**

```bash
pytest apps/help/tests/test_help.py -v
```

Expected: all existing tests still pass; new `test_successful_filter_agent_call` passes.

**Step 5: Lint**

```bash
ruff check apps/help/views.py apps/help/tests/test_help.py --fix
ruff format apps/help/views.py apps/help/tests/test_help.py
```

**Step 6: Commit**

```bash
git add apps/help/views.py apps/help/tests/test_help.py
git commit -m "fix: remove csrf_exempt from run_agent view; add filter agent response test"
```

---

### Task 1: Add NL state to filterComponent

**Files:**
- Modify: `templates/experiments/filters.html:233` (inside `Alpine.data('filterComponent', () => ({`)

The Alpine component object currently opens with `dateRangeOptions,` then `filterData: {...}`. Add four new properties directly after `filterData`:

**Step 1: Open the file and locate the insertion point**

In `filters.html`, find the block starting at line ~236:
```js
filterData: {
  showFilters: false,
  filters: [],
  loading: false,
  columns: filterColumns,
},
starredFilters: [],
```

**Step 2: Add NL state properties after `filterData`**

Insert after the `filterData` closing `},` and before `starredFilters`:

```js
nlQuery: '',
nlLoading: false,
nlError: '',
filterSlug: "{{ df_table_type }}",
```

**Step 3: Lint the template**

```bash
npx djlint templates/experiments/filters.html --check
```

Expected: no errors (or only pre-existing ones).

**Step 4: Commit**

```bash
git add templates/experiments/filters.html
git commit -m "feat: add nlQuery/nlLoading/nlError/filterSlug state to filterComponent"
```

---

### Task 2: Add `generateFiltersFromNL()` method

**Files:**
- Modify: `templates/experiments/filters.html` (inside the `Alpine.data` object, before the closing `}));`)

**Step 1: Locate the insertion point**

Find the last method in the component — `updateFilterProperty(...)` ends around line 897, followed by a blank line and then `}));`. Add the new method before that closing.

**Step 2: Add the method**

```js
async generateFiltersFromNL() {
  if (!this.nlQuery.trim()) return;
  this.nlLoading = true;
  this.nlError = '';
  const agentUrl = "{% url 'help:run_agent' request.team.slug 'filter' %}";
  const App = SiteJS.app;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000);
  try {
    const res = await fetch(agentUrl, {
      method: 'POST',
      credentials: 'same-origin',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': App.Cookies.get('csrftoken'),
      },
      body: JSON.stringify({ query: this.nlQuery, filter_slug: this.filterSlug }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      this.nlError = "Couldn't understand that query. Try rephrasing it.";
      return;
    }
    const listOperators = new Set(['any of', 'all of', 'excludes']);
    const newFilters = (data.response?.filters || [])
      .filter(item => this.filterData.columns[item.column])
      .map(item => {
        const isList = listOperators.has(item.operator);
        let selectedValues = [];
        let value = item.value;
        if (isList) {
          try { selectedValues = JSON.parse(item.value); } catch { selectedValues = []; }
          value = '';
        }
        return {
          column: item.column,
          operator: item.operator,
          value,
          selectedValues,
          availableOperators: this.filterData.columns[item.column].operators || [],
          showOptions: false,
          searchQuery: '',
          filteredOptions: [...(this.filterData.columns[item.column].options || [])],
        };
      });
    if (!newFilters.length) {
      this.nlError = "No matching filters found.";
      return;
    }
    this.filterData.filters = newFilters;
    this.triggerFilterChange();
  } catch {
    this.nlError = "Couldn't understand that query. Try rephrasing it.";
  } finally {
    clearTimeout(timeoutId);
    this.nlLoading = false;
  }
},
```

**Step 3: Lint**

```bash
npx djlint templates/experiments/filters.html --check
```

**Step 4: Commit**

```bash
git add templates/experiments/filters.html
git commit -m "feat: add generateFiltersFromNL method to filterComponent"
```

---

### Task 3: Clear `nlError` when the filter panel opens

**Files:**
- Modify: `templates/experiments/filters.html` — `toggleFilters()` method (around line 311)

**Step 1: Locate `toggleFilters()`**

```js
toggleFilters() {
  this.filterData.showFilters = !this.filterData.showFilters;
  if (this.filterData.showFilters) {
    if (!this.starredFilters.length) {
      this.loadStarredFilters();
    }
    if (this.filterData.filters.length === 0) {
      this.addFilter();
    }
  }
},
```

**Step 2: Add `nlError` reset on open**

```js
toggleFilters() {
  this.filterData.showFilters = !this.filterData.showFilters;
  if (this.filterData.showFilters) {
    this.nlError = '';
    if (!this.starredFilters.length) {
      this.loadStarredFilters();
    }
    if (this.filterData.filters.length === 0) {
      this.addFilter();
    }
  }
},
```

**Step 3: Lint**

```bash
npx djlint templates/experiments/filters.html --check
```

**Step 4: Commit**

```bash
git add templates/experiments/filters.html
git commit -m "fix: clear nlError when filter panel opens"
```

---

### Task 4: Add NL input HTML at top of filter panel

**Files:**
- Modify: `templates/experiments/filters.html:40` (the `<!-- Filters Panel -->` div)

**Step 1: Locate the insertion point**

Find line ~41:
```html
<!-- Filters Panel -->
<div x-show="filterData.showFilters" class="absolute left-0 mt-2 p-4 bg-base-100 border border-base-200 rounded-lg shadow-lg z-10 min-w-max max-w-screen-lg" x-cloak>
  <div class="space-y-2">
    <template x-for="(filter, index) in filterData.filters" ...>
```

**Step 2: Insert the NL input section**

Add the following block immediately after the opening `<div x-show="filterData.showFilters" ...>` tag, before the `<div class="space-y-2">` line:

```html
<!-- Natural Language Filter Input -->
<div class="mb-3">
  <div class="flex gap-2">
    <input
      type="text"
      x-model="nlQuery"
      @keydown.enter.debounce.300ms="generateFiltersFromNL()"
      placeholder="e.g. sessions from last week excluding WhatsApp"
      class="input input-sm input-bordered flex-1"
      :disabled="nlLoading"
    >
    <button
      type="button"
      @click="generateFiltersFromNL()"
      :disabled="!nlQuery.trim() || nlLoading || !filterSlug"
      class="btn btn-sm btn-primary"
    >
      <span x-show="!nlLoading">✨ Generate</span>
      <span x-show="nlLoading" x-cloak>
        <i class="fa-solid fa-spinner fa-spin"></i>
      </span>
    </button>
  </div>
  <div x-show="nlError" x-cloak class="mt-1 text-sm text-error" x-text="nlError"></div>
</div>
```

**Step 3: Lint**

```bash
npx djlint templates/experiments/filters.html --check
```

Expected: no new errors.

**Step 4: Manual smoke test**

Start the dev server:
```bash
inv runserver
```

Open any sessions list page that has the filter panel. Click "Filter" to open the panel. Verify:
- [ ] NL input box appears at the top with placeholder text
- [ ] "✨ Generate" button is disabled when input is empty
- [ ] Typing text enables the button
- [ ] Pressing Enter triggers generation (same as clicking Generate)
- [ ] Rapid double-Enter does not fire two requests (debounce)
- [ ] Clicking Generate shows spinner on button while loading
- [ ] On success, filter rows are populated below and the table updates
- [ ] Query text persists after success (allows refinement)
- [ ] Typing a nonsensical query shows the error message inline
- [ ] Error clears on the next Generate click
- [ ] Closing and reopening the panel shows a clean state (no stale error)
- [ ] If the LLM takes >30s the error message appears and the spinner stops

**Step 5: Commit**

```bash
git add templates/experiments/filters.html
git commit -m "feat: add natural language filter input to filter panel"
```
