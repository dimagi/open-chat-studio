import logging
import re
from unittest import mock

import pytest
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import BaseMessage, HumanMessage

from apps.chat.conversation import (
    SUMMARY_TOO_LARGE_ERROR_MESSAGE,
    _get_new_summary,
    _get_summarization_prompt_tokens_with_context,
    _reduce_summary_size,
    compress_chat_history,
)
from apps.chat.exceptions import ChatException
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.pipelines.models import PipelineChatHistoryModes
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
    history = compress_chat_history(
        chat,
        llm,
        max_token_limit=30,
        keep_history_len=10,
        input_messages=[],
        history_mode=PipelineChatHistoryModes.SUMMARIZE,
    )
    assert len(history) == 1
    assert len(llm.get_calls()) == 0


@mock.patch("apps.chat.conversation._get_new_summary")
def test_compress_history(mock_get_new_summary, chat):
    mock_get_new_summary.return_value = "Summary"
    llm = FakeLlmSimpleTokenCount(responses=[])

    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)
    result = compress_chat_history(chat, llm, 20, keep_history_len=5, input_messages=[])
    # 5 messages * 3 tokens + 2 tokens for summary
    assert len(result) == 6
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 10"
    assert ChatMessage.objects.get(id=result[1].additional_kwargs["id"]).summary == "Summary"
    mock_get_new_summary.assert_called_once()


@mock.patch("apps.chat.conversation._get_new_summary")
def test_compress_history_due_to_large_input(mock_get_new_summary, chat):
    for i in range(6):
        ChatMessage.objects.create(chat=chat, content=f"Hello-{i}", message_type=ChatMessageType.HUMAN)

    mock_get_new_summary.return_value = "Summary"
    llm = FakeLlmSimpleTokenCount(responses=[])
    input_messages = [HumanMessage("Hi this is a large")]
    # 1 message = 2 tokens. 2 summary tokens + 5 messages (keep_history_len=5) * 2 token each + 6 input_tokens is
    # 18 tokens total so we expect 2 messages to be removed to get it to 14 tokens, so 3 messages 1 summary message
    result = compress_chat_history(chat, llm, 15, keep_history_len=5, input_messages=input_messages)
    assert len(result) == 4
    assert result[0].content == "Summary"
    assert result[1].content == "Hello-3"
    assert ChatMessage.objects.get(id=result[1].additional_kwargs["id"]).summary == "Summary"
    mock_get_new_summary.assert_called_once()


@mock.patch("apps.chat.conversation._get_new_summary")
def test_compress_chat_history_with_need_for_compression_after_truncate(mock_get_new_summary, chat):
    """History is still over token limit after truncating to keep_history_len so more
    messages are removed"""
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)

    mock_get_new_summary.return_value = "Summary"
    llm = FakeLlmSimpleTokenCount(responses=[])
    result = compress_chat_history(chat, llm, 17, input_messages=[])
    # 5 messages * 3 tokens + 2 tokens for summary
    assert len(result) == 6
    assert result[0].content == "Summary"
    assert result[1].content == "Hello 10"
    mock_get_new_summary.assert_called_once()


def test_compress_chat_history_not_needed_with_existing_summary(chat):
    for i in range(15):
        summary = "Summary old" if i == 10 else None
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN, summary=summary)

    llm = FakeLlmSimpleTokenCount(responses=[])
    result = compress_chat_history(chat, llm, 20, input_messages=[])
    assert len(result) == 6
    assert result[0].content == "Summary old"
    assert result[1].content == "Hello 10"
    assert len(llm.get_calls()) == 0


@mock.patch("apps.chat.conversation._get_new_summary")
@mock.patch("apps.chat.conversation._reduce_summary_size")
def test_compression_with_large_summary(_reduce_summary_size, mock_get_new_summary, chat):
    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)

    llm = FakeLlmSimpleTokenCount(responses=[])
    summary_content = "Summary " * 20
    mock_get_new_summary.return_value = summary_content
    _reduce_summary_size.return_value = summary_content, 0
    result = compress_chat_history(chat, llm, 26, input_messages=[])
    # 1 message * 3 tokens + 21 tokens for summary
    assert len(result) == 2
    assert result[0].content == summary_content
    assert result[1].content == "Hello 14"
    assert mock_get_new_summary.call_count == 2


@mock.patch("apps.chat.conversation._get_new_summary")
@mock.patch("apps.chat.conversation._reduce_summary_size")
def test_compression_exhausts_history(_reduce_summary_size, mock_get_new_summary, chat):
    messages = ChatMessage.objects.bulk_create(
        [ChatMessage(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN) for i in range(15)]
    )

    llm = FakeLlmSimpleTokenCount(responses=[])
    summary_content = "Summary " * 20
    mock_get_new_summary.return_value = summary_content
    _reduce_summary_size.return_value = summary_content, 0
    result = compress_chat_history(chat, llm, 20, input_messages=[])
    assert len(result) == 1
    assert result[0].content == summary_content
    assert ChatMessage.objects.get(id=messages[-1].id).summary == summary_content
    assert mock_get_new_summary.call_count == 2


@mock.patch("apps.chat.conversation._get_new_summary")
@mock.patch("apps.chat.conversation._reduce_summary_size")
def test_compression_exhausts_history_and_pruned_memory(_reduce_summary_size, _get_new_summary, chat):
    token_counts = [
        80,  # initial history token count
        50,  # 'input_messages' token count
    ]

    class Llm(FakeLlmSimpleTokenCount):
        def get_num_tokens_from_messages(*args, **kwargs):
            # Force the while loop inside compress_chat_history_from_messages to run until the `history` array
            # is empty
            return token_counts.pop(0) if token_counts else 70

    def _clear_pruned_memory(llm, pruned_memory, summary, model_token_limit):
        # Simulate the while loop running until the pruned memory is cleared
        pruned_memory.clear()
        return "Summary"

    ChatMessage.objects.bulk_create(
        [ChatMessage(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN) for i in range(5)]
    )

    llm = Llm(responses=[])
    _get_new_summary.side_effect = _clear_pruned_memory
    _reduce_summary_size.return_value = "Summary", 0
    result = compress_chat_history(chat, llm, 70, input_messages=[])
    assert len(result) == 1
    last_message = ChatMessage.objects.order_by("created_at").last()
    assert last_message.summary == "Summary"


def test_get_new_summary_with_large_history():
    """Test that we can compress a large history into a summary without exceeding the token limit
    for the LLM. This isn't usually an issue since we generate summaries incrementally but when sessions
    are populated via the API we need to be able to compress the history in one go."""
    llm = FakeLlmSimpleTokenCount(responses=["Summary"])

    pruned_memory = [HumanMessage(f"Hello {i}") for i in range(20)]

    prompt_tokens, _ = _get_summarization_prompt_tokens_with_context(llm, None, [])
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


def test_get_new_summary_with_large_summary(caplog):
    llm = FakeLlmSimpleTokenCount(responses=["Summary"])

    pruned_memory = [HumanMessage(f"Hello {i}") for i in range(2)]

    prompt_tokens, _ = _get_summarization_prompt_tokens_with_context(llm, None, [])
    llm.max_token_limit = prompt_tokens + 10  # set below what we expect when generating the summary

    summary = "Summary " * 20
    new_summary = _get_new_summary(llm, pruned_memory, summary, llm.max_token_limit)
    assert new_summary == "Summary"
    assert len(llm.get_calls()) == 1
    assert caplog.record_tuples == [("ocs.bots", logging.ERROR, SUMMARY_TOO_LARGE_ERROR_MESSAGE)]


@mock.patch("apps.chat.conversation.MAX_UNCOMPRESSED_MESSAGES", 5)
@mock.patch("apps.chat.conversation._tokens_exceeds_limit")
@mock.patch("apps.chat.conversation._get_new_summary")
def test_summarization_is_forced_when_too_many_messages(_get_new_summary, _tokens_exceeds_limit, chat):
    """Summarization should be forced when the amount of messages exceed the `MAX_UNCOMPRESSED_MESSAGES` limit, even
    though the max token count is not reached
    """
    _tokens_exceeds_limit.return_value = False
    _get_new_summary.return_value = "Summary"

    for i in range(15):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)

    llm = FakeLlmSimpleTokenCount(responses=[])
    result = compress_chat_history(chat, llm, max_token_limit=250000, input_messages=[HumanMessage("Hi")])

    # The result length should be equal to MAX_UNCOMPRESSED_MESSAGES
    assert len(result) == 5

    # _tokens_exceeds_limit should have been called 3 times. 2 calls before pruning and 1 final call to exit the loop,
    # since we're removing the number of messages needed to get below the limit
    assert _tokens_exceeds_limit.call_count == 3


@pytest.mark.django_db()
def test_truncate_tokens(chat):
    class FakeLlm:
        def get_num_tokens_from_messages(self, messages):
            return sum(len(msg.content.split()) for msg in messages)

    history = [
        "Hello there",  # 2 tokens
        "This is a test message",  # 5 tokens
        "Another one",  # 2 tokens
        "Final message",  # 2 tokens
    ]

    for i, message in enumerate(history):
        ChatMessage.objects.create(
            chat=chat,
            content=message,
            message_type=ChatMessageType.AI if i % 2 else ChatMessageType.HUMAN,
        )

    llm = FakeLlm()
    result = compress_chat_history(
        chat, llm, 6, input_messages=[], history_mode=PipelineChatHistoryModes.TRUNCATE_TOKENS
    )

    remaining_after_pruning = ["Another one", "Final message"]
    assert [r.content for r in result] == remaining_after_pruning
    summary_message = ChatMessage.objects.get(
        chat=chat, metadata__compression_marker=PipelineChatHistoryModes.TRUNCATE_TOKENS
    )
    assert summary_message.content == "Another one"

    # Check that the compression marker is respected
    assert len(chat.get_langchain_messages_until_marker(marker=PipelineChatHistoryModes.TRUNCATE_TOKENS)) == 2


def test_get_new_summary_with_large_message():
    """Test that messages over 1000 words are trimmed and a summary is correctly generated in one pass."""
    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    llm.max_token_limit = 2000
    long_message = " ".join(["word"] * 1200)
    pruned_memory = [HumanMessage(long_message)]
    prompt_tokens, _ = _get_summarization_prompt_tokens_with_context(llm, None, [])

    new_summary = _get_new_summary(llm, pruned_memory, None, llm.max_token_limit)

    assert new_summary == "Summary"
    assert len(llm.get_calls()) == 1


def test_get_new_summary_with_large_message_raises_chat_exception():
    """Test if token count of single message exceeds max_token_limit then max recursion depth limit is exceeded"""
    # This should never ever happen in practice
    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    llm.max_token_limit = 500
    long_message = " ".join(["word"] * 1200)
    pruned_memory = [HumanMessage(long_message)]
    prompt_tokens, _ = _get_summarization_prompt_tokens_with_context(llm, None, [])
    with pytest.raises(ChatException):
        _get_new_summary(llm, pruned_memory, None, llm.max_token_limit)


def test_reduce_summary_size():
    """Tests that the _reduce_summary_size method successfully reduces a large summary
    by calling the LLM repeatedly until the summary is under the token limit"""
    llm = FakeLlmSimpleTokenCount(responses=["Shorter summary 1", "Even shorter 2", "Final 3"])
    initial_summary = "This is a very long summary " * 10  # Create a long summary
    summary_token_limit = 3  # Set a low token limit to force multiple reductions

    result, _ = _reduce_summary_size(llm, initial_summary, summary_token_limit)

    assert result == "Final 3"  # Should get the last response after multiple attempts
    assert len(llm.get_calls()) == 3  # Should have made 3 calls to reduce the size

    # Verify the compression prompts were used correctly
    calls = llm.get_calls()
    for i, call in enumerate(calls):
        if i == 0:
            assert initial_summary in call.args[0][0].content
        elif i == 1:
            assert "Shorter summary 1" in call.args[0][0].content
        elif i == 2:
            assert "Even shorter 2" in call.args[0][0].content


def test_reduce_summary_size_gives_up_after_three_attempts():
    """Tests that _reduce_summary_size gives up and returns an empty string
    if it can't reduce the summary enough after 3 attempts"""
    llm = FakeLlmSimpleTokenCount(responses=["Still too long " * 5, "Also too long " * 4, "Still too big " * 3])
    initial_summary = "This is a very long summary " * 10
    summary_token_limit = 3  # Impossible to meet this limit

    with pytest.raises(ChatException):
        _reduce_summary_size(llm, initial_summary, summary_token_limit)

    assert len(llm.get_calls()) == 3  # Should still make exactly 3 attempts


def test_reduce_summary_size_succeeds_early():
    """Tests that _reduce_summary_size stops making LLM calls once it gets
    a summary under the token limit"""
    llm = FakeLlmSimpleTokenCount(responses=["Short enough"])
    initial_summary = "This is a very long summary " * 5
    summary_token_limit = 5

    result, _ = _reduce_summary_size(llm, initial_summary, summary_token_limit)

    assert result == "Short enough"
    assert len(llm.get_calls()) == 1  # Should only make one call since first response was short enough


@pytest.mark.django_db()
def test_max_history_length_compression(chat):
    for i in range(8):
        ChatMessage.objects.create(chat=chat, content=f"Hello {i}", message_type=ChatMessageType.HUMAN)

    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    result = compress_chat_history(
        chat,
        llm,
        max_token_limit=20,
        input_messages=[],
        keep_history_len=5,
        history_mode=PipelineChatHistoryModes.MAX_HISTORY_LENGTH,
    )

    assert len(result) == 5
    assert [message.content for message in result] == [f"Hello {i}" for i in range(3, 8)]
