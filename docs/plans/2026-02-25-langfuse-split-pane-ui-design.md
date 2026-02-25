# Langfuse Trace UI — Split Pane Design

**Date:** 2026-02-25
**Status:** Approved

## Problem

The current Langfuse trace UI renders all observation spans fully expanded by default, with syntax-highlighted JSON blocks for input/output at every level. For traces with 10–20 spans this produces a very long page requiring extensive scrolling. Users have no overview of the full tree and no way to orient themselves within it.

**Primary use cases:**
- Where did this output come from? (complex pipelines)
- What was the exact failure and in which node?
- What data did this node receive from a tool?

All are targeted investigative workflows: find the right span fast, then inspect it deeply.

## Design

### Overall Layout

Replace the current vertically-expanding span tree with a fixed-height split pane container inside the existing Langfuse card. The card header (title + "Open in Langfuse" button) remains above the panes.

```
┌─ Langfuse Trace ──────────────── [Open in Langfuse ↗] ─┐
│ ┌─ Tree (30%) ──┬─ Span Detail (70%) ──────────────────┐ │
│ │               │                                       │ │
│ │  span list    │  selected span input/output           │ │
│ │  (scrollable) │  (scrollable)                         │ │
│ │               │                                       │ │
│ └───────────────┴───────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
```

Container height: `min-h-96 max-h-[600px]`. Both panes scroll independently.

Alpine.js holds `selectedSpanId` at the top-level `x-data`. On load, the right pane auto-selects the **first ERROR span** if one exists, otherwise the first span — so failures surface immediately.

### Left Pane — Tree Navigator (30% width)

Compact, scrollable list of all spans. Navigation only — no expand/collapse.

Each row contains:
- **Indentation** via `pl-[{depth * 12}px]` with a thin left-border connector line showing hierarchy
- **Status indicator** — small colored dot or badge: red (ERROR), yellow (WARN), muted (OK)
- **Span name** — truncated with ellipsis if too long (`truncate`)
- **Latency** — right-aligned, muted, `text-xs`

Selected span highlighted with `bg-primary/10`. Clicking any row sets `selectedSpanId` and updates the right pane immediately. Thin right border separates the pane from the detail view.

### Right Pane — Span Detail (70% width)

Shows details for the currently selected span. Sections:

1. **Header** — span name (larger text), status badge, latency, span type (`GENERATION`, `SPAN`, etc.)
2. **Status message** — shown prominently in red when ERROR, hidden otherwise
3. **Input** — readable text via existing `readable_value` filter by default; "Show raw JSON" toggle reveals syntax-highlighted JSON block
4. **Output** — same pattern as Input
5. **Empty state** — "Select a span to view details" placeholder, briefly visible before auto-select on load

Input/output sections are hidden entirely when a span has no value for that field.

### Auto-Select Behavior

Computed in `TraceLangufuseSpansView.get_context_data()`:
- Pass `auto_selected_span_id` in context: first ERROR span if any, otherwise first span in the flattened list
- Alpine initialises `selectedSpanId` from this value

### Template Data Requirements

The view already builds the parent-child observation map. Two additions needed:

1. **Flattened ordered list** — spans in display order (depth-first), each annotated with `depth: int`
2. **`auto_selected_span_id`** — as above

Both are pure Python, computed in the view, no extra API calls.

## Implementation Notes

- No layout changes outside the Langfuse card
- Existing error/unavailable states are unchanged
- `readable_value` and `highlight_json` filters reused as-is
- Alpine.js handles all interactivity; no HTMX needed for pane switching
- All span detail HTML rendered at load time, shown/hidden via Alpine `x-show`

## Edge Cases

| Case | Behaviour |
|------|-----------|
| Single span | Split pane renders normally; span auto-selected |
| All spans are errors | First span auto-selected; all rows tinted red |
| Long span names | Truncated in tree, full name in detail header |
| Span with no input/output | Those sections hidden in detail pane |
| Large JSON | Raw toggle off by default; pre block scrolls within pane |

## Testing

Extend `apps/trace/tests/test_langfuse_spans_view.py`:

- Flattened span list in context includes correct `depth` per span
- `auto_selected_span_id` is first ERROR span when errors exist
- `auto_selected_span_id` falls back to first span when no errors
- Split pane HTML structure present in success-state response
