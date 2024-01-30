import pytest

from apps.chat.models import Chat, ChatMessage, ChatMessageType


@pytest.fixture()
def chat(team_with_users):
    chat = Chat.objects.create(team=team_with_users)
    ChatMessage.objects.create(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
    return chat


def test_chat_get_langchain_messages_with_messages(chat):
    assert len(chat.get_langchain_messages()) == 1
    assert len(chat.get_langchain_messages_until_summary()) == 1


def test_chat_get_langchain_messages_until_summary_with_summary(chat):
    ChatMessage.objects.create(chat=chat, content="Hi", message_type=ChatMessageType.AI)
    ChatMessage.objects.create(
        chat=chat, content="What's up?", message_type=ChatMessageType.HUMAN, summary="Cordial greetings"
    )
    ChatMessage.objects.create(chat=chat, content="Nothin, what's up with you?", message_type=ChatMessageType.AI)
    assert len(chat.get_langchain_messages()) == 4
    assert len(chat.get_langchain_messages_until_summary()) == 3
    assert [(m.type, m.content) for m in chat.get_langchain_messages_until_summary()] == [
        ("system", "Cordial greetings"),
        ("human", "What's up?"),
        ("ai", "Nothin, what's up with you?"),
    ]


def test_chat_message_to_langchain_dict():
    chat = Chat()
    message = ChatMessage(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
    expected_dict = {
        "type": ChatMessageType.HUMAN,
        "data": {
            "content": "Hello",
            "additional_kwargs": {
                "id": message.id,
            },
        },
    }
    assert message.to_langchain_dict() == expected_dict


def test_chat_message_summary_to_langchain_dict():
    chat = Chat()
    message = ChatMessage(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN, summary="Summary")
    expected_dict = {
        "type": ChatMessageType.SYSTEM,
        "data": {
            "content": "Summary",
            "additional_kwargs": {
                "id": message.id,
            },
        },
    }
    assert message.summary_to_langchain_dict() == expected_dict
