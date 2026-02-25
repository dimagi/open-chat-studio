import json
from json import JSONDecodeError

from django import template
from django.core.serializers.json import DjangoJSONEncoder
from django.http import QueryDict
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def to_json(obj):
    """Source: https://gist.github.com/czue/90e287c9818ae726f73f5850c1b00f7f"""

    def escape_script_tags(unsafe_str):
        # seriously: http://stackoverflow.com/a/1068548/8207
        return unsafe_str.replace("</script>", '<" + "/script>')

    # json.dumps does not properly convert QueryDict array parameter to json
    if isinstance(obj, QueryDict):
        obj = dict(obj)
    try:
        json_string = json.dumps(obj, indent=2, cls=DjangoJSONEncoder)
        return mark_safe(escape_script_tags(json_string))
    except JSONDecodeError:
        return mark_safe("Unable to decode JSON data")


def _extract_text(content) -> str | None:
    """Extract plain text (and tool calls) from a string or list of content blocks.

    Handles text blocks ({type: "text", text: "..."}) and function_call blocks
    ({type: "function_call", name: "...", args: {...}}). Returns None if the input
    is neither a string nor a recognisable list of content blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
            elif block.get("type") == "function_call":
                name = block.get("name", "?")
                args = block.get("args", {})
                args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
                parts.append(f"→ {name}({args_str})")
        return "\n".join(filter(None, parts)) or None
    return None


@register.filter
def readable_value(value) -> str | None:
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

        # OCS span input shape: {"input": {"message_text": "...", ...}}
        nested = value.get("input")
        if isinstance(nested, dict):
            msg_text = nested.get("message_text")
            if isinstance(msg_text, str) and msg_text:
                return msg_text

    return None
