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

    # Verify timestamps increment by 1 second
    time_diff_1_to_2 = messages[1].created_at - messages[0].created_at
    time_diff_2_to_3 = messages[2].created_at - messages[1].created_at

    assert time_diff_1_to_2 == pytest.approx(timedelta(seconds=1), abs=timedelta(seconds=0.1))
    assert time_diff_2_to_3 == pytest.approx(timedelta(seconds=1), abs=timedelta(seconds=0.1))

    # Verify the first message's timestamp is in the past
    # (base_time should be len(history) seconds ago)
    assert messages[0].created_at < before_time


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
