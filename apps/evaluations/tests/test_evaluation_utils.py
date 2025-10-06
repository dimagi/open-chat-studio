import pytest

from apps.evaluations.exceptions import HistoryParseException
from apps.evaluations.utils import parse_history_text


def test_parse_history_functionality():
    """Test the history parsing functionality."""

    # Test empty history
    assert parse_history_text("") == []

    # Test single line history
    history_text = "user: Hello there"
    result = parse_history_text(history_text)
    assert len(result) == 1
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "Hello there"

    # Test multi-line history
    history_text = "user: Hello\nassistant: Hi there!\nuser: How are you?"
    result = parse_history_text(history_text)
    assert len(result) == 3
    assert result[0]["message_type"] == "human"
    assert result[1]["message_type"] == "ai"
    assert result[2]["message_type"] == "human"

    # Test message with newlines in content
    history_text = "user: This is a multi-line\nmessage with newlines\nassistant: I understand your\nmulti-line message"
    result = parse_history_text(history_text)
    assert len(result) == 2
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "This is a multi-line\nmessage with newlines"
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "I understand your\nmulti-line message"

    # Test messages with continuation lines (valid format)
    history_text = "user: Hello\nsome random text without role\nassistant: Hi there!\nmore garbled content"
    result = parse_history_text(history_text)
    assert len(result) == 2  # Only the valid human/ai messages should be parsed
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "Hello\nsome random text without role"  # Continuation line included
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "Hi there!\nmore garbled content"  # Continuation line included

    # Test different casings (HUMAN, Human, AI, Ai, etc.)
    history_text = "USER: Hello from uppercase\nAssistant: Mixed case response\nuser: lowercase again"
    result = parse_history_text(history_text)
    assert len(result) == 3
    assert result[0]["message_type"] == "human"  # Always normalized to lowercase
    assert result[0]["content"] == "Hello from uppercase"
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "Mixed case response"
    assert result[2]["message_type"] == "human"
    assert result[2]["content"] == "lowercase again"

    # Test validation: history text that doesn't start with user: or assistant: raises exception
    invalid_history_text = "This is just random text\nuser: without proper formatting"
    with pytest.raises(HistoryParseException):
        parse_history_text(invalid_history_text)

    # Test validation: history text starting with other roles raises exception
    invalid_history_text2 = "system: This is a system message\nuser: Hello"
    with pytest.raises(HistoryParseException):
        parse_history_text(invalid_history_text2)

    # Test validation: empty lines and whitespace should still work if first line is valid
    history_text_with_whitespace = "\n\n  user: Hello with whitespace  \n\n  assistant: Response  \n\n"
    result = parse_history_text(history_text_with_whitespace)
    assert len(result) == 2
    assert result[0]["message_type"] == "human"
    assert result[0]["content"] == "Hello with whitespace"
    assert result[1]["message_type"] == "ai"
    assert result[1]["content"] == "Response"
