# Sync Log Redesign — Design Doc

**Date:** 2026-02-18
**Issue:** #2124 follow-up
**Scope:** Template-only changes (`sync_logs_modal.html`, `sync_logs_list.html`)

## Problem

The current sync log modal (`sync_logs_modal.html`) renders a table inside a DaisyUI `<dialog>`. Because the page and modal share the same dark `bg-base-200`/`bg-base-300` background, the modal blends into the page behind it. The file list on the page is visible through or below the modal, making it visually ambiguous where the sync logs end and the file list begins.

## Decision

Keep the modal approach (opened via the "View Logs" button). Replace the flat table with **per-sync card entries** and give the `modal-box` a contrasting `bg-base-100` background.

## Design

### Modal container (`sync_logs_modal.html`)

- Add `bg-base-100` to `modal-box` so it visually pops against the page
- Retain: source icon + name in `<h3>`, close button, error-filter checkbox
- No structural changes to the HTMX wiring

### Sync entry cards (`sync_logs_list.html`)

Replace `<table>` with a `flex flex-col gap-3` list of cards. Each card:

```
┌─ Feb 18, 2026, 3:23 PM ─────────── ✓ Success ─┐
│  [+2 Added]  [~0 Updated]  [-0 Removed]  2.5s  │
└────────────────────────────────────────────────┘
```

**Card structure:**
- Container: `bg-base-200 rounded-lg p-3 flex flex-col gap-2`
- Row 1: date (`<time>`) on the left, status badge on the right — `flex justify-between items-center`
- Row 2: stat chips using `badge badge-sm badge-outline`:
  - `+N Added` → `badge-success`
  - `~N Updated` → `badge-info`
  - `-N Removed` → `badge-warning`
  - `Xs` duration → `badge-ghost` (no outline)
- Error row (when `log.error_message` is set): `<details>` disclosure with `bg-base-300` inset block, `<pre class="text-xs whitespace-pre-wrap">`
- When status is `failed` and no individual counts: chips hidden, only error disclosure shown

**Status badges:**
- Success → `badge badge-success badge-sm`
- Failed → `badge badge-error badge-sm`
- In Progress → `badge badge-warning badge-sm`

### Empty state

Centered `bg-base-200 rounded-lg` card with info icon and text. No change to logic.

### Pagination

No changes. Keep existing `btn btn-sm` join-style prev/next buttons.

## Files Changed

| File | Change |
|------|--------|
| `templates/documents/partials/sync_logs_modal.html` | Add `bg-base-100` to `modal-box` |
| `templates/documents/partials/sync_logs_list.html` | Replace `<table>` with card layout |

## Out of Scope

- No Python/view changes
- No migrations
- No changes to the files list or document source header
