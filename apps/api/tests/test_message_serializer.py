import pytest
from django.utils import timezone
from time_machine import travel

from apps.api.serializers import ChatMessageSerializer, MessageSerializer
from apps.chat.models import ChatMessage, ChatMessageType
from apps.chat.schemas import ModerationDetail

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


@pytest.mark.parametrize(
    "serializer_class",
    [pytest.param(MessageSerializer, id="message"), pytest.param(ChatMessageSerializer, id="chat_message")],
)
def test_message_serializer_omits_moderation_detail(serializer_class):
    message = ChatMessage(
        message_type=ChatMessageType.HUMAN,
        content="<script>",
        moderation_detail=ModerationDetail(rules=["markup"]),
    )

    data = serializer_class(instance=message).data

    assert "moderation_detail" not in data
