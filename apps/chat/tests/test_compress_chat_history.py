from unittest import mock

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


@pytest.fixture(autouse=True)
def _patch_initial_tokens():
    with mock.patch("apps.chat.conversation.INITIAL_SUMMARY_TOKENS_ESTIMATE", 2):
        yield


@pytest.fixture()
def chat(team_with_users):
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
    assert len(llm.get_calls()) == 0


def test_compress_history(chat, llm):
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)
    result = compress_chat_history(chat, llm, 20, keep_history_len=5)
    # 5 messages * 3 tokens + 2 tokens for summary
    assert len(result) == 6
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 10"
    assert ChatMessage.objects.get(id=result[1].additional_kwargs["id"]).summary == "Summary"
    assert len(llm.get_calls()) == 1


def test_compress_chat_history_with_need_for_compression_after_truncate(chat, llm):
    """History is still over token limit after truncating to keep_history_len so more
    messages are removed"""
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)
    result = compress_chat_history(chat, llm, 17)
    # 5 messages * 3 tokens + 2 tokens for summary
    assert len(result) == 6
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 10"
    assert len(llm.get_calls()) == 1


def test_compress_chat_history_not_needed_with_existing_summary(chat, llm):
    for i in range(15):
        summary = "Summary old" if i == 10 else None
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN, summary=summary)
    result = compress_chat_history(chat, llm, 20)
    assert len(result) == 6
    assert result[0].content == "Summary old"
    assert result[1].content == "Hello 10"
    assert len(llm.get_calls()) == 0


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
    assert len(llm.get_calls()) == 2


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
    assert len(llm.get_calls()) == 2
