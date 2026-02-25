from django.utils.safestring import SafeData

from apps.web.templatetags.json_tags import highlight_json, readable_value


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
