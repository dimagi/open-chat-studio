from datetime import timedelta

import pytest
from django.utils import timezone

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.evaluations.tasks import _create_message_history
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users():
    return TeamWithUsersFactory()


@pytest.fixture()
def chat(team_with_users):
    return Chat.objects.create(team=team_with_users, name="Test Chat")


@pytest.mark.django_db()
def test_create_message_history_increments_timestamps(chat):
    """Test that created_at timestamps increment by 1 second for each message"""
    history = [
        {"message_type": ChatMessageType.HUMAN, "content": "First message"},
        {"message_type": ChatMessageType.AI, "content": "Second message"},
        {"message_type": ChatMessageType.HUMAN, "content": "Third message"},
    ]

    # Capture time before creation
    before_time = timezone.now()

    _create_message_history(chat, history)

    # Retrieve messages in chronological order
    messages = list(ChatMessage.objects.filter(chat=chat).order_by("created_at"))

    assert len(messages) == 3

    # Verify each message has the correct content and type
    assert messages[0].content == "First message"
    assert messages[0].message_type == ChatMessageType.HUMAN
    assert messages[1].content == "Second message"
    assert messages[1].message_type == ChatMessageType.AI
    assert messages[2].content == "Third message"
    assert messages[2].message_type == ChatMessageType.HUMAN

    # Verify timestamps increment by exactly 1 second
    time_diff_1_to_2 = messages[1].created_at - messages[0].created_at
    time_diff_2_to_3 = messages[2].created_at - messages[1].created_at

    assert time_diff_1_to_2 == timedelta(seconds=1)
    assert time_diff_2_to_3 == timedelta(seconds=1)

    # Verify the first message's timestamp is in the past
    # (base_time should be len(history) seconds ago)
    assert messages[0].created_at < before_time


@pytest.mark.django_db()
def test_create_message_history_chronological_ordering(chat):
    """Test that messages can be retrieved in chronological order"""
    history = [
        {"message_type": ChatMessageType.HUMAN, "content": "Message 1"},
        {"message_type": ChatMessageType.AI, "content": "Message 2"},
        {"message_type": ChatMessageType.HUMAN, "content": "Message 3"},
        {"message_type": ChatMessageType.AI, "content": "Message 4"},
        {"message_type": ChatMessageType.HUMAN, "content": "Message 5"},
    ]

    _create_message_history(chat, history)

    # Retrieve using the default ordering (which should be created_at)
    messages = list(ChatMessage.objects.filter(chat=chat).order_by("created_at"))

    # Verify the messages are in the same order as the input
    for i, msg in enumerate(messages):
        assert msg.content == f"Message {i + 1}"


@pytest.mark.django_db()
def test_create_message_history_with_summary(chat):
    """Test that message history can include summaries"""
    history = [
        {
            "message_type": ChatMessageType.HUMAN,
            "content": "Question",
            "summary": "Previous conversation summary",
        },
        {"message_type": ChatMessageType.AI, "content": "Answer", "summary": None},
    ]

    _create_message_history(chat, history)

    messages = list(ChatMessage.objects.filter(chat=chat).order_by("created_at"))

    assert len(messages) == 2
    assert messages[0].summary == "Previous conversation summary"
    assert messages[1].summary is None


@pytest.mark.django_db()
def test_create_message_history_empty_list(chat):
    """Test that empty history list creates no messages"""
    history = []

    _create_message_history(chat, history)

    messages = ChatMessage.objects.filter(chat=chat)
    assert messages.count() == 0


@pytest.mark.django_db()
def test_create_message_history_single_message(chat):
    """Test with a single message"""
    history = [{"message_type": ChatMessageType.HUMAN, "content": "Single message"}]

    before_time = timezone.now()
    _create_message_history(chat, history)

    messages = list(ChatMessage.objects.filter(chat=chat))

    assert len(messages) == 1
    assert messages[0].content == "Single message"
    assert messages[0].created_at < before_time


@pytest.mark.django_db()
def test_create_message_history_defaults_to_human_type(chat):
    """Test that message_type defaults to HUMAN when not specified"""
    history = [
        {"content": "Message without type"},
        {"message_type": ChatMessageType.AI, "content": "Message with type"},
    ]

    _create_message_history(chat, history)

    messages = list(ChatMessage.objects.filter(chat=chat).order_by("created_at"))

    assert messages[0].message_type == ChatMessageType.HUMAN  # default
    assert messages[1].message_type == ChatMessageType.AI


@pytest.mark.django_db()
def test_create_message_history_large_list(chat):
    """Test with a larger number of messages to ensure scaling works"""
    num_messages = 50
    history = [
        {"message_type": ChatMessageType.HUMAN if i % 2 == 0 else ChatMessageType.AI, "content": f"Message {i}"}
        for i in range(num_messages)
    ]

    _create_message_history(chat, history)

    messages = list(ChatMessage.objects.filter(chat=chat).order_by("created_at"))

    assert len(messages) == num_messages

    # Verify all timestamps are sequential with 1-second increments
    for i in range(1, num_messages):
        time_diff = messages[i].created_at - messages[i - 1].created_at
        assert time_diff == timedelta(seconds=1)


@pytest.mark.django_db()
def test_create_message_history_base_time_calculation(chat):
    """Test that base_time is calculated correctly as (now - len(history)) seconds"""
    history = [
        {"message_type": ChatMessageType.HUMAN, "content": "First"},
        {"message_type": ChatMessageType.AI, "content": "Second"},
        {"message_type": ChatMessageType.HUMAN, "content": "Third"},
        {"message_type": ChatMessageType.AI, "content": "Fourth"},
    ]

    # The base_time should be 4 seconds in the past (len(history) = 4)
    # The first message should have created_at = base_time + 0 seconds
    # The last message should have created_at = base_time + 3 seconds

    before_time = timezone.now()
    _create_message_history(chat, history)
    after_time = timezone.now()

    messages = list(ChatMessage.objects.filter(chat=chat).order_by("created_at"))

    # The first message should be approximately 4 seconds before 'before_time'
    expected_first_time = before_time - timedelta(seconds=len(history))
    time_diff_from_expected = abs((messages[0].created_at - expected_first_time).total_seconds())

    # Allow for small timing differences (should be less than 1 second)
    assert time_diff_from_expected < 1.0

    # The last message should be approximately 1 second before 'before_time'
    expected_last_time = before_time - timedelta(seconds=1)
    time_diff_from_expected = abs((messages[-1].created_at - expected_last_time).total_seconds())
    assert time_diff_from_expected < 1.0

    # Verify the last message is still in the past
    assert messages[-1].created_at < after_time


@pytest.mark.django_db()
def test_create_message_history_preserves_content_and_summary(chat):
    """Test that all fields are properly preserved"""
    history = [
        {
            "message_type": ChatMessageType.HUMAN,
            "content": "What is the weather?",
            "summary": "User asked about weather",
        },
        {
            "message_type": ChatMessageType.AI,
            "content": "It's sunny today.",
            "summary": None,
        },
        {
            "message_type": ChatMessageType.HUMAN,
            "content": "What about tomorrow?",
            "summary": "Conversation about weather continues",
        },
    ]

    _create_message_history(chat, history)

    messages = list(ChatMessage.objects.filter(chat=chat).order_by("created_at"))

    assert messages[0].content == "What is the weather?"
    assert messages[0].summary == "User asked about weather"
    assert messages[0].message_type == ChatMessageType.HUMAN

    assert messages[1].content == "It's sunny today."
    assert messages[1].summary is None
    assert messages[1].message_type == ChatMessageType.AI

    assert messages[2].content == "What about tomorrow?"
    assert messages[2].summary == "Conversation about weather continues"
    assert messages[2].message_type == ChatMessageType.HUMAN
