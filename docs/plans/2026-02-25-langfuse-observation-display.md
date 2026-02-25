# Langfuse Observation Display Improvements

**Goal:** Make the Langfuse span tree more readable — start spans expanded and show human-readable input/output text instead of raw JSON by default.

---

## Background

The current observation template renders input/output as raw JSON in `<pre>` blocks and starts all spans collapsed. This means users must click to expand each span and then parse raw JSON to read the content. Real traces have 10–18 observations, making this laborious.

## Observation Data Patterns (from trace 684)

Four distinct input/output structures appear in practice:

| Type | Input format | Output format |
|------|-------------|---------------|
| GENERATION (`ChatOpenAI`) | List of `{role, content}` message dicts (OpenAI format) | `{role: "assistant", content: [{type: "text", text: "..."}]}` |
| SPAN (`ocs docs`, `Process Message`) | `{input: "hi"}` or `{input_state: {...}}` | `{response: "..."}` or `{content: "..."}` |
| TOOL (`file-search-by-index`) | Python repr string | `{content: "...long..."}` |
| CHAIN (`LangGraph`, `model`, etc.) | `{messages: [...LangChain state...], participant_data: {...}}` | Same or `None` |

## Design

### Change 1: Start spans expanded

In `langfuse_observation.html`, change `x-data="{ open: false }"` → `x-data="{ open: true }"`.

### Change 2: `readable_value` template filter

Add to `apps/web/templatetags/json_tags.py`. Extraction logic:

1. **String** → return as-is (handles TOOL input which is a Python repr string)
2. **List of message dicts** (has `role` key) → format each as `"**{role}**: {content}"`, joined by `\n\n`. Content may itself be a list of `{type, text}` blocks — extract and join the text parts.
3. **Dict with `role` + `content`** → treat as a single message, same content extraction as above
4. **Dict with a well-known simple-text key** — check in order: `response`, `content`, `input`, `bot_message`, `text` — return the string value of the first match
5. **Anything else** → return `None` (caller falls back to raw JSON)

Helper `_extract_text(content)`: handles content that is a string, or a list of `{type: "text", text: "..."}` blocks.

### Change 3: Template update in `langfuse_observation.html`

For each of `observation.input` and `observation.output`:

```
readable = value | readable_value
if readable:
    show readable text in a clean prose block
    show "Raw JSON ▾" Alpine toggle below (hidden by default, x-show="showRaw")
else:
    show raw JSON directly (no toggle, nothing to hide)
```

The Alpine state `showRaw` is scoped to each input/output panel independently using nested `x-data`.

The readable text block uses `whitespace-pre-wrap` so line breaks in multi-message output are preserved.

## Testing

- Unit tests for `readable_value` covering: plain string, OpenAI message list with text content blocks, single message dict, dict with `response` key, dict with `content` list blocks, unrecognised structure returns `None`.
- Existing `test_langfuse_spans_view.py` DB tests check page status codes and observation names — no changes needed since those assertions don't depend on JSON rendering.

## Files Changed

- `apps/web/templatetags/json_tags.py` — add `readable_value` filter
- `apps/web/tests/test_json_tags.py` — add unit tests (create if absent)
- `templates/trace/partials/langfuse_observation.html` — use filter, add raw JSON toggle, start expanded
