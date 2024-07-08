import pytest

from apps.api.serializers import MessageSerializer

CASES = [("system", "system"), ("human", "user"), ("ai", "assistant")]


@pytest.mark.parametrize(("type_", "role"), CASES)
def test_message_serializer_api_to_internal(type_, role):
    serializer = MessageSerializer(data={"role": role, "content": "hello"})
    assert serializer.is_valid()
    assert serializer.validated_data == {"type": type_, "message": "hello"}


@pytest.mark.parametrize(("type_", "role"), CASES)
def test_message_serializer_internal_to_api(type_, role):
    serializer = MessageSerializer(instance={"type": type_, "message": "hello"})
    assert serializer.data == {"role": role, "content": "hello"}
