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
