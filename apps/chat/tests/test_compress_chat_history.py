import pytest

from apps.chat.conversation import compress_chat_history
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.utils.langchain import FakeLlm


@pytest.fixture()
def chat(team_with_users):
    chat = Chat.objects.create(team=team_with_users)
    ChatMessage.objects.create(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
    return chat


@pytest.fixture()
def llm():
    llm = FakeLlm(responses=["Summary"], token_counts=[30, 20, 10])
    return llm


def test_compress_history_no_need_for_compression(chat, llm):
    history = compress_chat_history(chat, llm, max_token_limit=30, keep_history_len=10)
    assert len(history) == 1


def test_compress_chat_history_with_need_for_compression(chat, llm):
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)
    result = compress_chat_history(chat, llm, 20)
    assert len(result) == 11
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 5"
    assert ChatMessage.objects.get(id=result[1].additional_kwargs["id"]).summary == "Summary"


def test_compress_chat_history_with_need_for_compression_after_truncate(chat, llm):
    """History is still over token limit after truncating to keep_history_len so more
    messages are removed (1 in this case)"""
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)
    result = compress_chat_history(chat, llm, 10)
    print(result)
    assert len(result) == 10
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 6"


def test_compress_chat_history_second_compression(chat, llm):
    for i in range(15):
        summary = "Summary old" if i == 7 else None
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN, summary=summary)
    result = compress_chat_history(chat, llm, 20)
    assert len(result) == 9
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 7"
