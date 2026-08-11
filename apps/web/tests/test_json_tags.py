import html
import json

import pytest
from django.template import Context, Template
from django.utils.safestring import SafeData

from apps.web.templatetags.json_tags import format_participant_data_diff, highlight_json, readable_value, to_json


def _render_to_json(value):
    return Template("{% load json_tags %}{{ value|to_json }}").render(Context({"value": value}))


def _circular_dict():
    data = {}
    data["self"] = data
    return data


class TestToJson:
    def test_output_is_not_marked_safe(self):
        # unsafe output means Django's autoescaping runs over the JSON when it is rendered
        assert not isinstance(to_json({"key": "value"}), SafeData)

    def test_dumps_indented_json(self):
        assert to_json({"key": "value"}) == '{\n  "key": "value"\n}'

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param('<img src=x onerror="alert(1)">', id="img-onerror"),
            pytest.param("</script><script>alert(1)</script>", id="script-breakout"),
            pytest.param("<svg/onload=alert(1)>", id="svg-onload"),
        ],
    )
    def test_html_in_values_is_escaped_when_rendered(self, payload):
        rendered = _render_to_json({"participant_name": payload})
        assert "<" not in rendered
        assert ">" not in rendered
        assert "&lt;" in rendered

    def test_html_in_keys_is_escaped_when_rendered(self):
        rendered = _render_to_json({"<img src=x onerror=alert(1)>": "value"})
        assert "<" not in rendered
        assert "&lt;" in rendered

    def test_rendered_output_is_readable_json(self):
        # what the browser displays is unchanged: entities decode back to the original JSON
        rendered = _render_to_json({"greeting": "<b>hi</b>", "count": 2})
        assert json.loads(html.unescape(rendered)) == {"greeting": "<b>hi</b>", "count": 2}

    @pytest.mark.parametrize(
        "make_value",
        [
            pytest.param(lambda: {1, 2}, id="set-is-not-serializable"),
            pytest.param(lambda: {"obj": object()}, id="arbitrary-object"),
            pytest.param(_circular_dict, id="circular-reference"),
        ],
    )
    def test_unserializable_value_returns_message(self, make_value):
        # json.dumps raises TypeError for unsupported types and ValueError for circular data
        assert to_json(make_value()) == "Unable to encode JSON data"


class TestHighlightJson:
    def test_returns_safe_html(self):
        result = highlight_json({"key": "value"})
        assert isinstance(result, SafeData)

    def test_contains_syntax_spans(self):
        result = highlight_json({"key": "value"})
        assert "<span" in result

    def test_dict_value_appears_in_output(self):
        result = highlight_json({"hello": "world"})
        assert "hello" in result
        assert "world" in result

    def test_none_renders_as_null(self):
        result = highlight_json(None)
        assert "null" in result
        assert isinstance(result, SafeData)

    def test_list_renders(self):
        result = highlight_json([1, 2, 3])
        assert "1" in result
        assert isinstance(result, SafeData)


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
        result = readable_value(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi there"}, {"type": "other", "text": "ignored"}],
            }
        )
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

    def test_empty_list_returns_none(self):
        assert readable_value([]) is None

    def test_generation_output_with_tool_call(self):
        # GENERATION output when LLM calls a tool — content is a function_call block
        result = readable_value(
            {
                "role": "assistant",
                "content": [{"type": "function_call", "name": "search", "args": {"query": "hello"}}],
            }
        )
        assert result == "assistant: → search(query='hello')"

    def test_generation_output_mixed_text_and_tool_call(self):
        # LLM emits text then calls a tool in the same response
        result = readable_value(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me look that up."},
                    {"type": "function_call", "name": "search", "args": {"query": "hello"}},
                ],
            }
        )
        assert result == "assistant: Let me look that up.\n→ search(query='hello')"

    def test_span_input_with_message_text(self):
        # OCS span input shape: input key contains a dict with message_text
        result = readable_value({"input": {"message_text": "hi", "participant_id": "test@test.com"}})
        assert result == "hi"

    def test_span_input_with_message_text_empty_skipped(self):
        result = readable_value({"input": {"message_text": "", "participant_id": "test@test.com"}})
        assert result is None

    def test_function_call_with_non_dict_args_does_not_crash(self):
        # Sentry bug: args from external API may not be a dict
        result = readable_value(
            {
                "role": "assistant",
                "content": [{"type": "function_call", "name": "search", "args": "invalid"}],
            }
        )
        assert result == "assistant: → search('invalid')"

    def test_function_call_with_null_args_does_not_crash(self):
        result = readable_value(
            {
                "role": "assistant",
                "content": [{"type": "function_call", "name": "ping", "args": None}],
            }
        )
        assert result == "assistant: → ping(None)"

    def test_anthropic_tool_use_block(self):
        # Anthropic GENERATION output with tool_use block
        result = readable_value(
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tu_1", "name": "search", "input": {"query": "hello"}}],
            }
        )
        assert result == "assistant: → search(query='hello')"

    def test_anthropic_tool_use_block_no_input(self):
        result = readable_value(
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "ping", "input": {}}],
            }
        )
        assert result == "assistant: → ping()"

    def test_anthropic_tool_result_block_string(self):
        # tool_result with plain string content
        result = readable_value(
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "42 results found"}],
            }
        )
        assert result == "user: ← tool_result: 42 results found"

    def test_anthropic_tool_result_block_nested_text(self):
        # tool_result with list of text blocks as content
        result = readable_value(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": [{"type": "text", "text": "Found it."}],
                    }
                ],
            }
        )
        assert result == "user: ← tool_result: Found it."

    def test_anthropic_tool_result_block_empty_content(self):
        result = readable_value(
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": ""}],
            }
        )
        assert result == "user: ← tool_result"

    def test_anthropic_mixed_text_and_tool_use(self):
        result = readable_value(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll search for that."},
                    {"type": "tool_use", "name": "search", "input": {"query": "hello"}},
                ],
            }
        )
        assert result == "assistant: I'll search for that.\n→ search(query='hello')"


class TestFormatParticipantDataDiff:
    def test_format_diff_add(self):
        diff = [["add", "", [["score", 100]]]]
        result = format_participant_data_diff(diff)
        assert len(result) == 1
        assert result[0]["type"] == "add"
        assert result[0]["path"] == "score"
        assert result[0]["value"] == repr(100)

    def test_format_diff_remove(self):
        diff = [["remove", "", [["old_key", "old_val"]]]]
        result = format_participant_data_diff(diff)
        assert len(result) == 1
        assert result[0]["type"] == "remove"
        assert result[0]["path"] == "old_key"
        assert result[0]["value"] == repr("old_val")

    def test_format_diff_change(self):
        diff = [["change", "plan", ["free", "pro"]]]
        result = format_participant_data_diff(diff)
        assert len(result) == 1
        assert result[0]["type"] == "change"
        assert result[0]["path"] == "plan"
        assert result[0]["old"] == repr("free")
        assert result[0]["new"] == repr("pro")

    def test_format_diff_nested_path(self):
        diff = [["change", "preferences.lang", ["en", "fr"]]]
        result = format_participant_data_diff(diff)
        assert result[0]["path"] == "preferences.lang"

    def test_format_diff_list_path(self):
        diff = [["change", ["tags", 0], ["old", "new"]]]
        result = format_participant_data_diff(diff)
        assert result[0]["path"] == "tags.0"

    def test_format_diff_multiple_adds_in_one_entry(self):
        diff = [["add", "", [["name", "Alice"], ["age", 30]]]]
        result = format_participant_data_diff(diff)
        assert len(result) == 2
        assert result[0]["path"] == "name"
        assert result[1]["path"] == "age"

    def test_format_diff_empty(self):
        assert format_participant_data_diff([]) == []

    def test_format_diff_nested_add(self):
        diff = [["add", "preferences", [["theme", "dark"]]]]
        result = format_participant_data_diff(diff)
        assert result[0]["path"] == "preferences.theme"
