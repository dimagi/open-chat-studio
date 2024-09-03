import re
from unittest import mock

import pytest
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import BaseMessage, HumanMessage

from apps.chat.conversation import _get_new_summary, _get_summary_tokens_with_context, compress_chat_history
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.utils.langchain import FakeLlm


class FakeLlmSimpleTokenCount(FakeLlm):
    max_token_limit: int | None = None

    def get_num_tokens(self, text: str) -> int:
        return len(text.split())

    def get_num_tokens_from_messages(self, messages: list) -> int:
        return BaseLanguageModel.get_num_tokens_from_messages(self, messages)

    def _call(self, messages: list[BaseMessage], *args, **kwargs) -> str | BaseMessage:
        if self.max_token_limit is not None:
            token_count = self.get_num_tokens_from_messages(messages)
            if token_count > self.max_token_limit:
                raise Exception(f"Token limit exceeded: {token_count} > {self.max_token_limit}")
        return super()._call(messages, *args, **kwargs)


@pytest.fixture(autouse=True)
def _patch_initial_tokens():
    with mock.patch("apps.chat.conversation.INITIAL_SUMMARY_TOKENS_ESTIMATE", 2):
        yield


@pytest.fixture()
def chat(team_with_users):
    return Chat.objects.create(team=team_with_users)


def test_compress_history_no_need_for_compression(chat):
    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    ChatMessage.objects.create(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
    history = compress_chat_history(chat, llm, max_token_limit=30, keep_history_len=10, input_messages=[])
    assert len(history) == 1
    assert len(llm.get_calls()) == 0


def test_compress_history(chat):
    llm = FakeLlmSimpleTokenCount(responses=["Summary"])

    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)
    result = compress_chat_history(chat, llm, 20, keep_history_len=5, input_messages=[])
    # 5 messages * 3 tokens + 2 tokens for summary
    assert len(result) == 6
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 10"
    assert ChatMessage.objects.get(id=result[1].additional_kwargs["id"]).summary == "Summary"
    assert len(llm.get_calls()) == 1


def test_compress_history_due_to_large_input(chat):
    for i in range(6):
        ChatMessage.objects.create(chat=chat, content=f"Hello-{i}", message_type=ChatMessageType.HUMAN)

    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    input_messages = [HumanMessage("Hi this is a large")]
    # 1 message = 2 tokens. 2 summary tokens + 5 messages (keep_history_len=5) * 2 token each + 6 input_tokens is
    # 18 tokens total so we expect 2 messages to be removed to get it to 14 tokens, so 3 messages 1 summary message
    result = compress_chat_history(chat, llm, 15, keep_history_len=5, input_messages=input_messages)
    assert len(result) == 4
    assert result[0].content == "Summary"
    assert result[1].content == "Hello-3"
    assert ChatMessage.objects.get(id=result[1].additional_kwargs["id"]).summary == "Summary"
    assert len(llm.get_calls()) == 1


def test_compress_chat_history_with_need_for_compression_after_truncate(chat):
    """History is still over token limit after truncating to keep_history_len so more
    messages are removed"""
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)

    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    result = compress_chat_history(chat, llm, 17, input_messages=[])
    # 5 messages * 3 tokens + 2 tokens for summary
    assert len(result) == 6
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 10"
    assert len(llm.get_calls()) == 1


def test_compress_chat_history_not_needed_with_existing_summary(chat):
    for i in range(15):
        summary = "Summary old" if i == 10 else None
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN, summary=summary)

    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    result = compress_chat_history(chat, llm, 20, input_messages=[])
    assert len(result) == 6
    assert result[0].content == "Summary old"
    assert result[1].content == "Hello 10"
    assert len(llm.get_calls()) == 0


def test_compression_with_large_summary(chat):
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)

    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    summary_content = "Summary " * 20
    llm.responses = [summary_content]
    result = compress_chat_history(chat, llm, 26, input_messages=[])
    # 1 message * 3 tokens + 21 tokens for summary
    assert len(result) == 2
    assert result[0].content == summary_content
    assert result[1].content == "Hello 14"
    assert len(llm.get_calls()) == 2


def test_compression_exhausts_history(chat):
    messages = ChatMessage.objects.bulk_create(
        [ChatMessage(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN) for i in range(15)]
    )

    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    summary_content = "Summary " * 20
    llm.responses = [summary_content]
    result = compress_chat_history(chat, llm, 20, input_messages=[])
    assert len(result) == 1
    assert result[0].content == summary_content
    assert ChatMessage.objects.get(id=messages[-1].id).summary == summary_content
    assert len(llm.get_calls()) == 2


def test_get_new_summary_with_large_history():
    """Test that we can compress a large history into a summary without exceeding the token limit
    for the LLM. This isn't usually an issue since we generate summaries incrementally but when sessions
    are populated via the API we need to be able to compress the history in one go."""
    llm = FakeLlmSimpleTokenCount(responses=["Summary"])

    pruned_memory = [HumanMessage(f"Hello {i}") for i in range(20)]

    prompt_tokens, _ = _get_summary_tokens_with_context(llm, None, [])
    # token limit below what we expect when generating the summary (20 * 3 = 60 + prompt_tokens)
    llm.max_token_limit = prompt_tokens + 30 - 5  # set low enough to force 2 iterations

    new_summary = _get_new_summary(llm, pruned_memory, None, llm.max_token_limit)
    assert new_summary is not None
    assert len(llm.get_calls()) == 3

    # check that message ordering is correct
    messages_in_call = []
    for messages in llm.get_call_messages():
        messages_in_call.append(re.findall(r"Hello (\d+)", messages[0].content))
    assert messages_in_call == [
        ["0", "1", "2", "3", "4", "5", "6", "7"],
        ["8", "9", "10", "11", "12", "13", "14", "15"],
        ["16", "17", "18", "19"],
    ]
