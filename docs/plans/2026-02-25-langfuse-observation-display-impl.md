# Langfuse Observation Display Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Start Langfuse spans expanded by default and show human-readable input/output text with a collapsible raw JSON toggle.

**Architecture:** A new `readable_value` template filter in `json_tags.py` extracts plain text from common Langfuse observation value shapes (strings, OpenAI message lists, dicts with well-known keys). The `langfuse_observation.html` template is updated to start expanded, use the filter for primary display, and show a raw JSON toggle via Alpine.js only when a readable form exists.

**Tech Stack:** Django template filters, Alpine.js (`x-data`, `x-show`, `x-text`), DaisyUI, pytest (no DB needed for filter tests).

---

### Task 1: Add `readable_value` filter — TDD

**Files:**
- Modify: `apps/web/templatetags/json_tags.py`
- Create: `apps/web/tests/test_json_tags.py`

**Step 1: Write the failing tests**

Create `apps/web/tests/test_json_tags.py`:

```python
import pytest

from apps.web.templatetags.json_tags import readable_value


class TestReadableValue:
    def test_none_returns_none(self):
        assert readable_value(None) is None

    def test_plain_string_returned_as_is(self):
        assert readable_value("hello") == "hello"

    def test_empty_string_returned_as_is(self):
        assert readable_value("") == ""

    def test_openai_messages_list_with_string_content(self):
        messages = [
            {"role": "system", "content": "You are a bot."},
            {"role": "user", "content": "hi"},
        ]
        result = readable_value(messages)
        assert result == "system: You are a bot.\n\nuser: hi"

    def test_openai_messages_list_with_content_blocks(self):
        # GENERATION input — content is a list of {type, text} blocks
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello there"}]},
        ]
        result = readable_value(messages)
        assert result == "user: hello there"

    def test_openai_messages_skips_items_without_role(self):
        items = [{"foo": "bar"}, {"role": "user", "content": "hi"}]
        result = readable_value(items)
        assert result == "user: hi"

    def test_openai_messages_all_without_role_returns_none(self):
        result = readable_value([{"foo": "bar"}])
        assert result is None

    def test_single_message_dict_with_string_content(self):
        # GENERATION output shape
        result = readable_value({"role": "assistant", "content": "Hello!"})
        assert result == "assistant: Hello!"

    def test_single_message_dict_with_content_blocks(self):
        result = readable_value({
            "role": "assistant",
            "content": [{"type": "text", "text": "Hi there"}, {"type": "other", "text": "ignored"}],
        })
        assert result == "assistant: Hi there"

    def test_dict_response_key(self):
        result = readable_value({"response": "Here is my answer."})
        assert result == "Here is my answer."

    def test_dict_content_key_string(self):
        result = readable_value({"content": "Some content."})
        assert result == "Some content."

    def test_dict_input_key_string(self):
        result = readable_value({"input": "hi"})
        assert result == "hi"

    def test_dict_bot_message_key(self):
        result = readable_value({"bot_message": "Hello user!"})
        assert result == "Hello user!"

    def test_dict_input_key_non_string_skipped(self):
        # input value is a nested dict — not a plain string, skip it
        result = readable_value({"input": {"nested": "dict"}})
        assert result is None

    def test_dict_content_key_list_skipped(self):
        # content is a list (not string) and no role key — not a message dict
        result = readable_value({"content": [1, 2, 3]})
        assert result is None

    def test_unrecognised_dict_returns_none(self):
        result = readable_value({"messages": [{"type": "human"}], "session_state": {}})
        assert result is None

    def test_integer_returns_none(self):
        result = readable_value(42)
        assert result is None

    def test_key_priority_response_before_content(self):
        # response key checked before content key
        result = readable_value({"response": "answer", "content": "other"})
        assert result == "answer"
```

**Step 2: Run tests to verify they fail**

```bash
pytest apps/web/tests/test_json_tags.py -v
```

Expected: `ImportError` — `readable_value` does not exist yet.

**Step 3: Implement `readable_value` in `json_tags.py`**

Add after the existing `to_json` filter:

```python
def _extract_text(content) -> str | None:
    """Extract plain text from a string or a list of {type, text} content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(filter(None, parts)) or None
    return None


@register.filter
def readable_value(value):
    """Extract a human-readable string from a Langfuse observation input/output value.

    Returns None when no readable form can be extracted; the caller should
    fall back to displaying raw JSON.
    """
    if value is None:
        return None

    # Plain string (e.g. TOOL input which arrives as a Python repr string)
    if isinstance(value, str):
        return value

    # List of message dicts — OpenAI chat format used by GENERATION observations
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict) and "role" in item:
                text = _extract_text(item.get("content", ""))
                if text:
                    lines.append(f"{item['role']}: {text}")
        return "\n\n".join(lines) or None

    if isinstance(value, dict):
        # Single message dict (e.g. GENERATION output: {role, content})
        if "role" in value and "content" in value:
            text = _extract_text(value["content"])
            if text:
                return f"{value['role']}: {text}"

        # Dict with a well-known simple-text key
        for key in ("response", "content", "input", "bot_message", "text"):
            v = value.get(key)
            if isinstance(v, str) and v:
                return v

    return None
```

**Step 4: Run tests to verify they pass**

```bash
pytest apps/web/tests/test_json_tags.py -v
```

Expected: all 18 tests PASS.

**Step 5: Lint**

```bash
ruff check apps/web/templatetags/json_tags.py apps/web/tests/test_json_tags.py --fix
ruff format apps/web/templatetags/json_tags.py apps/web/tests/test_json_tags.py
```

**Step 6: Commit**

```bash
git add apps/web/templatetags/json_tags.py apps/web/tests/test_json_tags.py
git commit -m "feat: add readable_value template filter for Langfuse observation display"
```

---

### Task 2: Update `langfuse_observation.html`

**Files:**
- Modify: `templates/trace/partials/langfuse_observation.html`

**Step 1: Replace the template contents**

Replace the entire file with:

```html
{% load default_tags json_tags %}
{% with children=child_observations_map|get_item:observation.id %}
  <div x-data="{ open: true }" class="border border-base-200 rounded-lg">
    {# Header row — always visible #}
    <div class="flex items-center gap-3 px-4 py-2 cursor-pointer hover:bg-base-50"
         @click="open = !open">
      <button class="btn btn-xs btn-ghost btn-circle"
              :aria-label="open ? 'Collapse' : 'Expand'">
        <i class="fa-solid fa-chevron-right transition-transform"
           :class="open ? 'rotate-90' : ''"></i>
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
          {% with readable=observation.input|readable_value %}
            <div>
              <div class="text-xs font-medium text-base-content/60 mb-1">Input</div>
              {% if readable %}
                <div class="text-sm bg-base-200 rounded p-2 whitespace-pre-wrap">{{ readable }}</div>
                <div x-data="{ showRaw: false }" class="mt-1">
                  <button class="text-xs text-base-content/40 hover:text-base-content/70"
                          @click="showRaw = !showRaw"
                          x-text="showRaw ? 'Hide raw JSON ▴' : 'Raw JSON ▾'"></button>
                  <pre x-show="showRaw"
                       class="text-xs bg-base-200 rounded p-2 overflow-x-auto whitespace-pre-wrap mt-1">{{ observation.input|to_json }}</pre>
                </div>
              {% else %}
                <pre class="text-xs bg-base-200 rounded p-2 overflow-x-auto whitespace-pre-wrap">{{ observation.input|to_json }}</pre>
              {% endif %}
            </div>
          {% endwith %}
        {% endif %}
        {% if observation.output %}
          {% with readable=observation.output|readable_value %}
            <div>
              <div class="text-xs font-medium text-base-content/60 mb-1">Output</div>
              {% if readable %}
                <div class="text-sm bg-base-200 rounded p-2 whitespace-pre-wrap">{{ readable }}</div>
                <div x-data="{ showRaw: false }" class="mt-1">
                  <button class="text-xs text-base-content/40 hover:text-base-content/70"
                          @click="showRaw = !showRaw"
                          x-text="showRaw ? 'Hide raw JSON ▴' : 'Raw JSON ▾'"></button>
                  <pre x-show="showRaw"
                       class="text-xs bg-base-200 rounded p-2 overflow-x-auto whitespace-pre-wrap mt-1">{{ observation.output|to_json }}</pre>
                </div>
              {% else %}
                <pre class="text-xs bg-base-200 rounded p-2 overflow-x-auto whitespace-pre-wrap">{{ observation.output|to_json }}</pre>
              {% endif %}
            </div>
          {% endwith %}
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

**Step 2: Run full trace test suite**

```bash
pytest apps/trace/ apps/web/tests/test_json_tags.py -v
```

Expected: all tests PASS (existing view tests don't assert on JSON rendering, so no changes needed there).

**Step 3: Lint template**

```bash
python -m djlint templates/trace/partials/langfuse_observation.html --check
```

Fix any issues, then reformat if needed:

```bash
python -m djlint templates/trace/partials/langfuse_observation.html --reformat
```

**Step 4: Commit**

```bash
git add templates/trace/partials/langfuse_observation.html
git commit -m "feat: show readable input/output in Langfuse spans, start expanded"
```

---

### Task 3: Final verification

**Step 1: Run full suite for all touched apps**

```bash
pytest apps/trace/ apps/web/ -v
```

Expected: all tests PASS.

**Step 2: Lint**

```bash
ruff check apps/web/templatetags/json_tags.py apps/web/tests/test_json_tags.py --fix
ruff format apps/web/templatetags/json_tags.py apps/web/tests/test_json_tags.py
python -m djlint templates/trace/partials/ --check
```
