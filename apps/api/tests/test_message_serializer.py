import pytest
from django.utils import timezone
from time_machine import travel

from apps.api.serializers import MessageSerializer
from apps.chat.models import ChatMessage, ChatMessageType

CASES = [(ChatMessageType.SYSTEM, "system"), (ChatMessageType.HUMAN, "user"), (ChatMessageType.AI, "assistant")]


@pytest.mark.parametrize(("type_", "role"), CASES)
def test_message_serializer_api_to_internal(type_, role):
    serializer = MessageSerializer(data={"role": role, "content": "hello"})
    assert serializer.is_valid()
    assert serializer.validated_data == {"message_type": type_, "content": "hello"}


@pytest.mark.parametrize(("type_", "role"), CASES)
@travel("2021-01-01T12:00:00Z", tick=False)
def test_message_serializer_internal_to_api(type_, role):
    now = timezone.now()
    serializer = MessageSerializer(instance=ChatMessage(created_at=now, message_type=type_, content="hello"))
    assert serializer.data == {
        "created_at": "2021-01-01T12:00:00Z",
        "role": role,
        "content": "hello",
        "metadata": {},
        "tags": [],
        "attachments": [],
    }
