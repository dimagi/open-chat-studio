# Sync Log Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the flat table in the sync log modal with per-sync card entries and give the modal a contrasting background so it's visually distinct from the page behind it.

**Architecture:** Template-only change — no Python, no migrations, no view logic. Two files: `sync_logs_modal.html` gets `bg-base-100` on its `modal-box`, and `sync_logs_list.html` replaces `<table>` with a flex column of DaisyUI cards. All existing HTMX wiring and pagination logic is preserved.

**Tech Stack:** Django templates, DaisyUI (Tailwind CSS), HTMX

**Design doc:** `docs/plans/2026-02-18-sync-log-redesign-design.md`

---

### Task 1: Add `bg-base-100` to modal box

**Files:**
- Modify: `templates/documents/partials/sync_logs_modal.html`

**Step 1: Open and read the file**

Read `templates/documents/partials/sync_logs_modal.html`. The current `modal-box` line is:

```html
<div class="modal-box max-w-4xl">
```

**Step 2: Add background class**

Change it to:

```html
<div class="modal-box max-w-4xl bg-base-100">
```

That is the only change to this file.

**Step 3: Lint the template**

Run: `npx djlint templates/documents/partials/sync_logs_modal.html --check`

Expected: no errors (or pre-existing warnings only — do not introduce new ones).

**Step 4: Commit**

```bash
git add templates/documents/partials/sync_logs_modal.html
git commit -m "style: give sync logs modal a contrasting background"
```

---

### Task 2: Replace sync log table with card layout

**Files:**
- Modify: `templates/documents/partials/sync_logs_list.html`

**Step 1: Read the current file**

Read `templates/documents/partials/sync_logs_list.html` to confirm the current structure before editing.

**Step 2: Replace the entire file content**

The new template. Key points:
- Outer container: `flex flex-col gap-3` — one card per log entry
- Each card: `bg-base-200 rounded-lg p-3 flex flex-col gap-2`
- Row 1: `flex justify-between items-center` — date on left, status badge on right
- Row 2: stat chips using `badge badge-sm badge-outline` with color coding
- Error row: `<details>` only when `log.error_message` is set; hidden when status is failed and no message
- Pagination: unchanged from original

Replace the full file with:

```django
{% if sync_logs %}
  <div class="flex flex-col gap-3">
    {% for log in sync_logs %}
      <div class="bg-base-200 rounded-lg p-3 flex flex-col gap-2">
        <div class="flex justify-between items-center">
          <time class="text-sm font-medium" datetime="{{ log.sync_date.isoformat }}" title="{{ log.sync_date.isoformat }}">
            {{ log.sync_date|date:"DATETIME_FORMAT" }}
          </time>
          {% if log.status == "success" %}
            <span class="badge badge-success badge-sm">Success</span>
          {% elif log.status == "failed" %}
            <span class="badge badge-error badge-sm">Failed</span>
          {% else %}
            <span class="badge badge-warning badge-sm">In Progress</span>
          {% endif %}
        </div>
        {% if log.status != "failed" or log.files_added or log.files_updated or log.files_removed %}
          <div class="flex flex-wrap gap-2">
            <span class="badge badge-sm badge-success badge-outline">+{{ log.files_added }} Added</span>
            <span class="badge badge-sm badge-info badge-outline">~{{ log.files_updated }} Updated</span>
            <span class="badge badge-sm badge-warning badge-outline">-{{ log.files_removed }} Removed</span>
            {% if log.duration_seconds %}
              <span class="badge badge-sm badge-ghost">{{ log.duration_seconds|floatformat:1 }}s</span>
            {% endif %}
          </div>
        {% endif %}
        {% if log.error_message %}
          <details class="collapse collapse-arrow bg-base-300 rounded-lg">
            <summary class="collapse-title min-h-0 py-1 px-2 text-xs cursor-pointer">
              View Error
            </summary>
            <div class="collapse-content">
              <pre class="text-xs whitespace-pre-wrap">{{ log.error_message }}</pre>
            </div>
          </details>
        {% endif %}
      </div>
    {% endfor %}
  </div>

  {% if sync_logs.has_other_pages %}
    <div class="flex justify-center gap-2 mt-4">
      {% if sync_logs.has_previous %}
        <button
          class="btn btn-sm"
          hx-get="{% url 'documents:document_source_sync_logs' team.slug collection.id document_source.id %}?page={{ sync_logs.previous_page_number }}{% if show_errors_only %}&errors_only=true{% endif %}"
          hx-target="#sync_logs_content_{{ document_source.id }}"
          hx-swap="innerHTML"
        >
          <i class="fa-solid fa-chevron-left"></i> Previous
        </button>
      {% endif %}
      <span class="btn btn-sm btn-ghost">
        Page {{ sync_logs.number }} of {{ sync_logs.paginator.num_pages }}
      </span>
      {% if sync_logs.has_next %}
        <button
          class="btn btn-sm"
          hx-get="{% url 'documents:document_source_sync_logs' team.slug collection.id document_source.id %}?page={{ sync_logs.next_page_number }}{% if show_errors_only %}&errors_only=true{% endif %}"
          hx-target="#sync_logs_content_{{ document_source.id }}"
          hx-swap="innerHTML"
        >
          Next <i class="fa-solid fa-chevron-right"></i>
        </button>
      {% endif %}
    </div>
  {% endif %}
{% else %}
  <div class="flex items-center gap-2 bg-base-200 rounded-lg p-3">
    <i class="fa-solid fa-info-circle"></i>
    <span>No sync logs found{% if show_errors_only %} with errors{% endif %}.</span>
  </div>
{% endif %}
```

**Step 3: Lint the template**

Run: `npx djlint templates/documents/partials/sync_logs_list.html --check`

Expected: no new errors.

**Step 4: Run existing view tests to confirm nothing broke**

Run: `pytest apps/documents/tests/test_views.py::TestDocumentSourceSyncLogs -v`

Expected: all tests pass. The tests check for "Success" and "Failed" text — both still appear in the badge spans of the card layout.

**Step 5: Commit**

```bash
git add templates/documents/partials/sync_logs_list.html
git commit -m "style: replace sync log table with card layout"
```

---

### Task 3: Verify the full test suite for documents

**Step 1: Run all document tests**

Run: `pytest apps/documents/tests/ -v`

Expected: all tests pass.

**Step 2: Done**

No further changes needed. The redesign is template-only and all existing functionality (HTMX filtering, pagination, error display) is preserved.
