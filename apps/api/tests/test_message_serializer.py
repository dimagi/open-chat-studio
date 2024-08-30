import pytest

from apps.api.serializers import MessageSerializer
from apps.chat.models import ChatMessage, ChatMessageType

CASES = [(ChatMessageType.SYSTEM, "system"), (ChatMessageType.HUMAN, "user"), (ChatMessageType.AI, "assistant")]


@pytest.mark.parametrize(("type_", "role"), CASES)
def test_message_serializer_api_to_internal(type_, role):
    serializer = MessageSerializer(data={"role": role, "content": "hello"})
    assert serializer.is_valid()
    assert serializer.validated_data == {"message_type": type_, "content": "hello"}


@pytest.mark.parametrize(("type_", "role"), CASES)
def test_message_serializer_internal_to_api(type_, role):
    serializer = MessageSerializer(instance=ChatMessage(message_type=type_, content="hello"))
    assert serializer.data == {"role": role, "content": "hello"}
