import pytest
from django.utils import timezone
from time_machine import travel

from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_signal_human_activity_on_chat_messages():
    """
    Verify that first_activity_at is set only once and remains unchanged,
    while last_activity_at updates with each new message.

    This is the main test that validates the time-based behavior requested.
    """
    with travel("2025-01-01 10:00:00", tick=False):
        session = ExperimentSessionFactory()
        first_time = timezone.now()

        # Create first message
        ChatMessage.objects.create(
            chat=session.chat,
            content="First message",
            message_type=ChatMessageType.HUMAN,
        )
        session.refresh_from_db()

        # Both should be set to first_time
        assert session.first_activity_at == first_time
        assert session.last_activity_at == first_time

    # Travel 2 hours into the future
    with travel("2025-01-01 12:00:00", tick=False):
        second_time = timezone.now()

        # Create second message
        ChatMessage.objects.create(
            chat=session.chat,
            content="Second message",
            message_type=ChatMessageType.HUMAN,
        )

    # Travel another 2 hours into the future but with the IA response
    with travel("2025-01-01 14:00:00", tick=False):
        # Create IA response message
        ChatMessage.objects.create(
            chat=session.chat,
            content="Second message",
            message_type=ChatMessageType.AI,
        )

    session.refresh_from_db()

    # first_activity_at should NOT change - it remains the original time
    assert session.first_activity_at == first_time

    # last_activity_at should update to the last human message time
    assert session.last_activity_at == second_time
