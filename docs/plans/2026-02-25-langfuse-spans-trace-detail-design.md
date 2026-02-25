# Design: Langfuse Span Tree on OCS Trace Detail Page

**Date:** 2026-02-25
**Branch:** sk/langfuse

## Problem

The OCS trace detail page (`/traces/<pk>/`) shows OCS-internal span data. When an experiment has a Langfuse tracing provider configured, the Langfuse trace ID and URL are stored in `ChatMessage.metadata['trace_info']`, but the rich span/observation tree from Langfuse is not fetched or displayed.

## Goal

When viewing an OCS trace, fetch the full Langfuse span/observation tree and display it on the trace detail page. Also display a prominent "View in Langfuse" link.

## Approach

New HTMX lazy-load endpoint: `GET /traces/<pk>/langfuse-spans/`. The trace detail page includes a placeholder `<div>` that triggers this request after the main page loads, avoiding Langfuse API latency blocking page render.

## Architecture

### New URL & View

```
GET /a/<team_slug>/traces/<int:pk>/langfuse-spans/
→ TraceLangufuseSpansView (apps/trace/views.py)
→ renders trace/partials/langfuse_spans.html
```

### Data Flow

1. Load OCS `Trace` (already team-scoped via existing mixin)
2. Check `trace.output_message.trace_info` for an entry with `trace_provider == "langfuse"` — extract `trace_id` and `trace_url`
3. Get Langfuse credentials from `trace.experiment.trace_provider` (a `TraceProvider` model instance)
4. Create Langfuse client via `client_manager.get(config)` (reuses existing client management)
5. Fetch trace observations from Langfuse API
6. Render partial with span tree

### Early-exit conditions (return "not available" partial, no error)

- `trace.output_message` is None
- No Langfuse entry in `trace_info`
- `trace.experiment.trace_provider` is None

### Error state

- Langfuse API call fails (network error, auth error, trace not found) → render "could not load" partial, include fallback link to Langfuse URL if available

## Template: `trace/partials/langfuse_spans.html`

- **Header:** "View in Langfuse" button (opens new tab) linking to the trace URL
- **Span tree:** collapsible hierarchy using Alpine.js
  - Each span: name, status badge (colour-coded), duration
  - Input/output collapsed by default, expandable inline
  - Error message shown if status is ERROR
- **Loading state:** spinner shown in placeholder div while HTMX request is in flight (standard `htmx-indicator`)
- **Not available:** friendly message, no error styling
- **API error:** "Could not load Langfuse trace data" message + fallback Langfuse link if URL is known

## Trace Detail Page Changes (`trace/trace_detail.html`)

Add a new section below the existing inputs/outputs section:

```html
<div hx-get="{% url 'trace:trace_langfuse_spans' trace.pk %}"
     hx-trigger="load"
     hx-swap="outerHTML">
  <!-- spinner shown here while loading -->
</div>
```

## Testing

Unit tests in `apps/trace/tests/`:

| Scenario | Expected |
|---|---|
| Langfuse provider configured, API returns observations | Span tree rendered |
| No `trace_provider` on experiment | "Not available" partial |
| `trace_info` has no Langfuse entry | "Not available" partial |
| `output_message` is None | "Not available" partial |
| Langfuse API raises exception | "Could not load" partial with fallback link |

Mock the Langfuse client call at the integration boundary — do not test the SDK itself.

## Files Changed

| File | Change |
|---|---|
| `apps/trace/views.py` | Add `TraceLangufuseSpansView` |
| `apps/trace/urls.py` | Add URL for new view |
| `templates/trace/trace_detail.html` | Add HTMX placeholder div |
| `templates/trace/partials/langfuse_spans.html` | New partial template |
| `apps/trace/tests/test_langfuse_spans_view.py` | New test file |
