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
            },
        },
    }
    assert summary_message.to_langchain_dict() == expected_dict


def test_message_iterator_exclude_message_id(chat):
    msg1 = ChatMessage.objects.create(chat=chat, content="First")
    msg2 = ChatMessage.objects.create(chat=chat, content="Second")
    msg3 = ChatMessage.objects.create(chat=chat, content="Third")

    # Without exclusion, should get all messages
    all_messages = list(chat.message_iterator(with_summaries=False))
    assert msg1 in all_messages
    assert msg2 in all_messages
    assert msg3 in all_messages

    # With exclusion, should not get msg3
    messages_without_msg3 = list(chat.message_iterator(with_summaries=False, exclude_message_id=msg3.id))
    assert msg1 in messages_without_msg3
    assert msg2 in messages_without_msg3
    assert msg3 not in messages_without_msg3
