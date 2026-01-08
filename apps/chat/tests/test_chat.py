import pytest

from apps.chat.models import Chat, ChatMessage, ChatMessageType


@pytest.fixture()
def chat(team_with_users):
    chat = Chat.objects.create(team=team_with_users)
    ChatMessage.objects.create(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
    return chat


def test_chat_get_langchain_messages_until_marker(chat):
    ChatMessage.objects.create(chat=chat, content="Hi", message_type=ChatMessageType.AI)
    ChatMessage.objects.create(
        chat=chat,
        content="What's up?",
        message_type=ChatMessageType.HUMAN,
        summary="Cordial greetings",
        metadata={"compression_marker": "truncate_tokens"},
    )
    ChatMessage.objects.create(chat=chat, content="Nothin, what's up with you?", message_type=ChatMessageType.AI)
    assert len(chat.get_langchain_messages()) == 4
    messages = chat.get_langchain_messages_until_marker(marker="summarize")
    assert len(messages) == 3
    assert [(m.type, m.content) for m in messages] == [
        ("system", "Cordial greetings"),
        ("human", "What's up?"),
        ("ai", "Nothin, what's up with you?"),
    ]

    messages = chat.get_langchain_messages_until_marker(marker="truncate_tokens")
    assert len(messages) == 2
    assert [(m.type, m.content) for m in messages] == [
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
                "message_url": None,
            },
        },
    }
    assert message.to_langchain_dict() == expected_dict


def test_chat_message_summary_to_langchain_dict():
    chat = Chat()
    message = ChatMessage(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN, summary="Summary")
    summary_message = message.get_summary_message()
    assert summary_message.is_summary

    expected_dict = {
        "type": ChatMessageType.SYSTEM,
        "data": {
            "content": "Summary",
            "additional_kwargs": {
                "id": message.id,
                "message_url": None,
            },
        },
    }
    assert summary_message.to_langchain_dict() == expected_dict
