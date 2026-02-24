# Natural Language Filter Input Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a natural language input to the top of the filter panel that calls the existing `FilterAgent` backend and populates filter rows from the response.

**Architecture:** Extend the existing `filterComponent` Alpine.js object in `templates/experiments/filters.html` with three new state properties (`nlQuery`, `nlLoading`, `nlError`) and one new method (`generateFiltersFromNL`). No backend changes — the `/a/<team_slug>/help/filter/` endpoint is already ready. The entire change is one template file.

**Tech Stack:** Alpine.js, Fetch API, Django template tags, DaisyUI/Tailwind CSS

---

### Task 1: Add NL state to filterComponent

**Files:**
- Modify: `templates/experiments/filters.html:233` (inside `Alpine.data('filterComponent', () => ({`)

The Alpine component object currently opens with `dateRangeOptions,` then `filterData: {...}`. Add three new properties directly after `filterData`:

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
```

**Step 3: Lint the template**

```bash
npx djlint templates/experiments/filters.html --check
```

Expected: no errors (or only pre-existing ones).

**Step 4: Commit**

```bash
git add templates/experiments/filters.html
git commit -m "feat: add nlQuery/nlLoading/nlError state to filterComponent"
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
  const filterSlug = "{{ df_table_type }}";
  const App = SiteJS.app;
  try {
    const res = await fetch(agentUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': App.Cookies.get('csrftoken'),
      },
      body: JSON.stringify({ query: this.nlQuery, filter_slug: filterSlug }),
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
    this.filterData.filters = newFilters.length ? newFilters : this.filterData.filters;
    if (newFilters.length) this.triggerFilterChange();
  } catch {
    this.nlError = "Couldn't understand that query. Try rephrasing it.";
  } finally {
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

### Task 3: Add NL input HTML at top of filter panel

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
      @keydown.enter="generateFiltersFromNL()"
      placeholder="e.g. sessions from last week excluding WhatsApp"
      class="input input-sm input-bordered flex-1"
      :disabled="nlLoading"
    >
    <button
      type="button"
      @click="generateFiltersFromNL()"
      :disabled="!nlQuery.trim() || nlLoading"
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
- [ ] Clicking Generate shows spinner on button while loading
- [ ] On success, filter rows are populated below and the table updates
- [ ] Query text persists after success (allows refinement)
- [ ] Typing a nonsensical query shows the error message inline
- [ ] Error clears on the next Generate click

**Step 5: Commit**

```bash
git add templates/experiments/filters.html
git commit -m "feat: add natural language filter input to filter panel"
```
