import pytest
from langchain_core.language_models import BaseLanguageModel

from apps.chat.conversation import compress_chat_history
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.utils.langchain import FakeLlm


class FakeLlmSimpleTokenCount(FakeLlm):
    def get_num_tokens(self, text: str) -> int:
        return len(text.split())

    def get_num_tokens_from_messages(self, messages: list) -> int:
        return BaseLanguageModel.get_num_tokens_from_messages(self, messages)


@pytest.fixture()
def chat(team_with_users):
    # TODO: change to using unsaved / mock instance
    chat = Chat.objects.create(team=team_with_users)
    ChatMessage.objects.create(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
    return chat


@pytest.fixture()
def llm():
    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    return llm


def test_compress_history_no_need_for_compression(chat, llm):
    history = compress_chat_history(chat, llm, max_token_limit=30, keep_history_len=10)
    assert len(history) == 1


def test_compress_chat_history_with_need_for_compression(chat, llm):
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)
    result = compress_chat_history(chat, llm, 20)
    # 6 messages * 3 tokens + 2 tokens for summary
    assert len(result) == 7
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 9"
    assert ChatMessage.objects.get(id=result[1].additional_kwargs["id"]).summary == "Summary"


def test_compress_chat_history_with_need_for_compression_after_truncate(chat, llm):
    """History is still over token limit after truncating to keep_history_len so more
    messages are removed (1 in this case)"""
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)
    result = compress_chat_history(chat, llm, 17)
    # 5 messages * 3 tokens + 2 tokens for summary
    assert len(result) == 6
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 10"


def test_compress_chat_history_not_needed_with_summary(chat, llm):
    for i in range(15):
        summary = "Summary old" if i == 10 else None
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN, summary=summary)
    result = compress_chat_history(chat, llm, 20)
    assert len(result) == 6
    assert result[0].content == "Summary old"
    assert result[1].content == "Hello 10"


def test_compression_with_large_summary(chat, llm):
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)

    summary_content = "Summary " * 20
    llm.responses = [summary_content]
    result = compress_chat_history(chat, llm, 26)
    # 1 message * 3 tokens + 21 tokens for summary
    assert len(result) == 2
    assert result[0].content == summary_content
    assert result[1].content == "Hello 14"


def test_compression_exhausts_history(chat, llm):
    messages = ChatMessage.objects.bulk_create(
        [ChatMessage(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN) for i in range(15)]
    )

    summary_content = "Summary " * 20
    llm.responses = [summary_content]
    result = compress_chat_history(chat, llm, 20)
    assert len(result) == 1
    assert result[0].content == summary_content
    assert ChatMessage.objects.get(id=messages[-1].id).summary == summary_content
