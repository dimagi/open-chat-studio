import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.exceptions import HistoryParseException
from apps.evaluations.utils import make_message_pairs_from_queryset, parse_history_text
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory


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


@pytest.mark.django_db()
def test_make_message_pairs_from_queryset():
    chat = ChatFactory()

    # AI seed (first message) + Human + AI + Human + AI
    ai_seed = ChatMessageFactory(chat=chat, message_type=ChatMessageType.AI, content="Hi! I'm your assistant.")

    human1 = ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.HUMAN,
        content="Hello",
    )

    ai1 = ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.AI,
        content="How can I help?",
    )

    ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.HUMAN,
        content="I need help with coding",
    )

    ChatMessageFactory(
        chat=chat,
        message_type=ChatMessageType.AI,
        content="Sure! What language?",
    )

    # Test with AI seed message and subsequent pairs
    queryset = ChatMessage.objects.filter(id__in=[ai_seed.id, human1.id, ai1.id])
    result = make_message_pairs_from_queryset(queryset)

    assert len(result) == 3
    assert ai_seed in result
    assert human1 in result
    assert ai1 in result

    # Starting with human message (no AI seed)
    chat2 = ChatFactory()

    human_first = ChatMessageFactory(
        chat=chat2,
        message_type=ChatMessageType.HUMAN,
        content="Hello first",
    )

    ai_first = ChatMessageFactory(
        chat=chat2,
        message_type=ChatMessageType.AI,
        content="Hi back!",
    )

    human_second = ChatMessageFactory(
        chat=chat2,
        message_type=ChatMessageType.HUMAN,
        content="How are you?",
    )

    ai_second = ChatMessageFactory(
        chat=chat2,
        message_type=ChatMessageType.AI,
        content="I'm good!",
    )

    # Test with human message in queryset - should add corresponding AI messages
    queryset2 = ChatMessage.objects.filter(id__in=[human_first.id, human_second.id])
    result2 = make_message_pairs_from_queryset(queryset2)

    # Should include: human_first, ai_first, human_second, ai_second
    assert len(result2) == 4
    assert human_first in result2
    assert ai_first in result2
    assert human_second in result2
    assert ai_second in result2

    # AI message in queryset - should add corresponding human
    queryset3 = ChatMessage.objects.filter(id__in=[ai_first.id])
    result3 = make_message_pairs_from_queryset(queryset3)

    # Should include: human_first, ai_first
    assert len(result3) == 2
    assert human_first in result3
    assert ai_first in result3

    # Error case - AI message without corresponding human (not seed)
    chat3 = ChatFactory()
    ChatMessageFactory(
        chat=chat3,
        message_type=ChatMessageType.AI,
        content="Seed AI message",
    )
    # Create AI message that's not first but has no human before it
    orphaned_ai = ChatMessageFactory(
        chat=chat3,
        message_type=ChatMessageType.AI,
        content="Orphaned AI message",
    )

    queryset4 = ChatMessage.objects.filter(id=orphaned_ai.id)

    with pytest.raises(ValueError, match=r"AI message \d+ has no corresponding human message"):
        make_message_pairs_from_queryset(queryset4)

    # Error case - Human message without corresponding AI
    chat4 = ChatFactory()

    orphaned_human = ChatMessageFactory(
        chat=chat4, message_type=ChatMessageType.HUMAN, content="Orphaned human message"
    )

    queryset5 = ChatMessage.objects.filter(id=orphaned_human.id)

    with pytest.raises(ValueError, match=r"Human message \d+ has no corresponding AI message"):
        make_message_pairs_from_queryset(queryset5)

    # Mixed queryset with both human and AI messages
    chat5 = ChatFactory()

    h1 = ChatMessageFactory(chat=chat5, message_type=ChatMessageType.HUMAN, content="Question 1")

    a1 = ChatMessageFactory(
        chat=chat5,
        message_type=ChatMessageType.AI,
        content="Answer 1",
    )

    h2 = ChatMessageFactory(
        chat=chat5,
        message_type=ChatMessageType.HUMAN,
        content="Question 2",
    )

    a2 = ChatMessageFactory(
        chat=chat5,
        message_type=ChatMessageType.AI,
        content="Answer 2",
    )

    # Test with mixed queryset - both H1 and A2
    queryset6 = ChatMessage.objects.filter(id__in=[h1.id, a2.id])
    result6 = make_message_pairs_from_queryset(queryset6)

    # Should include all 4 messages (complete pairs)
    assert len(result6) == 4
    assert h1 in result6  # from queryset
    assert a1 in result6  # added as pair for h1
    assert h2 in result6  # added as pair for a2
    assert a2 in result6  # from queryset
