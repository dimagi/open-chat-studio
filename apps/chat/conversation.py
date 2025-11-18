import contextlib
import logging

from langchain.memory.prompt import SUMMARY_PROMPT
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, get_buffer_string, trim_messages
from langchain_core.prompts import (
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
)
from langchain_core.prompts.prompt import PromptTemplate

from apps.chat.exceptions import ChatException
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.pipelines.models import PipelineChatHistory, PipelineChatHistoryModes, PipelineChatMessages
from apps.utils.prompt import OcsPromptTemplate

COMPRESSION_MARKER = "__compression_marker__"

SUMMARY_TOO_LARGE_ERROR_MESSAGE = "Unable to compress chat history: existing summary too large"
MESSAGES_TOO_LARGE_ERROR_MESSAGE = (
    "Unable to compress chat history: Messages are too large for the context window of {tokens} tokens"
)
INITIAL_SUMMARY_TOKENS_ESTIMATE = 20
# The maximum number of messages that can be uncompressed
MAX_UNCOMPRESSED_MESSAGES = 1000

_SUMMARY_COMPRESSION_TEMPLATE = """Compress this summary into a shorter summary, about half of its original size:
    SUMMARY:
    {summary}

    Shorter summary:
"""

SUMMARY_COMPRESSION_PROMPT = PromptTemplate.from_template(_SUMMARY_COMPRESSION_TEMPLATE)

log = logging.getLogger("ocs.bots")


class BasicConversation:
    """
    A wrapper class that provides a single way/API to interact with the LLMs, regardless of it being a normal
    conversation or agent implementation
    """

    def __init__(
        self,
        prompt_str: str,
        source_material: str,
        llm: BaseChatModel,
    ):
        self.prompt_str = prompt_str
        self.source_material = source_material
        self.llm = llm
        self._build_chain()

        self.messages = []

    def _build_chain(self):
        prompt = OcsPromptTemplate.from_messages(
            [
                self.system_prompt,
                MessagesPlaceholder(variable_name="history"),
                HumanMessagePromptTemplate.from_template("{input}"),
            ]
        )

        # set output_key to match agent's output_key
        self.chain = prompt | self.llm

    @property
    def system_prompt(self):
        prompt_to_use = SystemMessagePromptTemplate.from_template(self.prompt_str)
        if self.source_material:
            with contextlib.suppress(KeyError):
                prompt_to_use = prompt_to_use.format(source_material=self.source_material)
        return prompt_to_use

    def load_memory_from_messages(self, messages: list[BaseMessage]):
        self.messages = messages

    def load_memory_from_chat(self, chat: Chat, max_token_limit: int):
        self.load_memory_from_messages(self._get_optimized_history(chat, max_token_limit))

    def _get_optimized_history(self, chat: Chat, max_token_limit: int) -> list[BaseMessage]:
        return compress_chat_history(chat, self.llm, max_token_limit, input_messages=[])

    def predict(self, input: str) -> tuple[str, int, int]:
        response = self.chain.invoke({"input": input, "history": self.messages})
        usage = response.usage_metadata or {}
        return response.content, usage.get("input_tokens", 0), usage.get("output_tokens", 0)


def compress_chat_history(
    chat: Chat,
    llm: BaseChatModel,
    max_token_limit: int,
    input_messages: list,
    keep_history_len: int = 10,
    history_mode: str = PipelineChatHistoryModes.SUMMARIZE,
) -> list[BaseMessage]:
    if history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        return list(
            reversed(
                [message.to_langchain_message() for message in chat.messages.order_by("-created_at")[:keep_history_len]]
            )
        )

    history_mode = history_mode or PipelineChatHistoryModes.SUMMARIZE
    history_messages = chat.get_langchain_messages_until_marker(marker=history_mode)
    try:
        history, last_message, summary = _compress_chat_history(
            history=history_messages,
            llm=llm,
            max_token_limit=max_token_limit,
            input_messages=input_messages,
            keep_history_len=keep_history_len,
            history_mode=history_mode,
        )
        if summary is not None:
            if last_message:
                if summary == COMPRESSION_MARKER:
                    try:
                        message = ChatMessage.objects.get(id=last_message.additional_kwargs["id"])
                        message.metadata["compression_marker"] = history_mode
                        message.save(update_fields=["metadata"])
                    except ChatMessage.DoesNotExist:
                        pass
                    return history
                else:
                    ChatMessage.objects.filter(id=last_message.additional_kwargs["id"]).update(summary=summary)
                    if summary:
                        history.insert(0, SystemMessage(content=summary))
                    return history
            else:
                logging.exception(f"last_message is unexpectedly None for chat_id={chat.id}")
        return history
    except (NameError, ImportError, ValueError, NotImplementedError):
        # typically this is because a library required to count tokens isn't installed
        log.exception("Unable to compress history")
        return history_messages


def compress_pipeline_chat_history(
    pipeline_chat_history: PipelineChatHistory,
    llm: BaseChatModel,
    max_token_limit: int,
    input_messages: list,
    keep_history_len: int = 10,
    history_mode: str = None,
) -> list[BaseMessage]:
    if history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        limit = keep_history_len // 2 + 1  # each pipeline history message is a pair
        messages = pipeline_chat_history.messages.order_by("-created_at")[:limit]
        langchain_messages = [
            message
            for message_pair in messages
            for message in message_pair.as_langchain_messages(include_summary=False)
        ]
        return list(reversed(langchain_messages))[:keep_history_len]

    history_mode = history_mode or PipelineChatHistoryModes.SUMMARIZE
    history_messages = pipeline_chat_history.get_langchain_messages_until_marker(history_mode)
    try:
        history, last_message, summary = _compress_chat_history(
            history=history_messages,
            llm=llm,
            max_token_limit=max_token_limit,
            input_messages=input_messages,
            keep_history_len=keep_history_len,
            history_mode=history_mode,
        )
        if summary is not None:
            if last_message:
                updates = {"compression_marker": history_mode}
                if summary != COMPRESSION_MARKER:
                    updates["summary"] = summary
                PipelineChatMessages.objects.filter(id=last_message.additional_kwargs["id"]).update(**updates)
                return (
                    [SystemMessage(content=summary)] + history
                    if history_mode == PipelineChatHistoryModes.SUMMARIZE
                    else history
                )
            else:
                logging.exception(f"last_message is unexpectedly None for chat_id={pipeline_chat_history.id}")
        return history

    except (NameError, ImportError, ValueError, NotImplementedError):
        # typically this is because a library required to count tokens isn't installed
        log.exception("Unable to compress history")
        return history_messages


def _compress_chat_history(
    history: list,
    llm: BaseChatModel,
    max_token_limit: int,
    input_messages: list,
    keep_history_len: int = 10,
    history_mode: str = PipelineChatHistoryModes.SUMMARIZE,
) -> tuple[list[BaseMessage], BaseMessage | None, str | None]:
    """Compresses the chat history to be less than max_token_limit tokens long. This will summarize the history
    if necessary and save the summary to the DB.
    """
    if history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        raise ValueError("History mode cannot be MAX_HISTORY_LENGTH for this function")

    if max_token_limit <= 0 or not history:
        log.info("Skipping chat history compression")
        return history, None, None

    total_messages = history.copy()
    if input_messages and input_messages[0].type == "system" and total_messages and total_messages[0].type == "system":
        # Move prompt and summary to the start of the list
        # Avoids the Anthropic error: received multiple non-consecutive system messages
        total_messages = [input_messages[0], total_messages[0]] + total_messages[1:] + input_messages[1:]
    else:
        total_messages.extend(input_messages)
    current_token_count = llm.get_num_tokens_from_messages(total_messages)
    if history_mode in [PipelineChatHistoryModes.SUMMARIZE, PipelineChatHistoryModes.TRUNCATE_TOKENS, None]:
        if current_token_count <= max_token_limit and len(total_messages) <= MAX_UNCOMPRESSED_MESSAGES:
            log.info("Skipping chat history compression: %s <= %s", current_token_count, max_token_limit)
            return history, None, None

    log.debug(
        "Compressing chat history with mode %s: %s/%s(max) tokens",
        history_mode,
        current_token_count,
        max_token_limit,
    )
    history, last_message, summary = compress_chat_history_from_messages(
        llm, history, keep_history_len, max_token_limit, input_messages, history_mode
    )
    return history, last_message, summary


def truncate_tokens(history, max_token_limit, llm, input_message_tokens) -> list[BaseMessage]:
    """Removes old messages until the token count is below the max limit."""
    return trim_messages(
        history,
        # Keep the last <= n_count tokens of the messages.
        strategy="last",
        token_counter=llm.get_num_tokens_from_messages,
        max_tokens=max_token_limit - input_message_tokens,
        start_on="human",
        end_on="ai",
        include_system=True,
        allow_partial=False,
    )


def summarize_history(llm, history, max_token_limit, input_message_tokens, summary, input_messages, pruned_memory):
    if input_message_tokens >= max_token_limit:
        raise ChatException(
            "Unable to compress history: input message tokens >= max token limit: "
            f"{input_message_tokens} > {max_token_limit}",
        )
    history_tokens = llm.get_num_tokens_from_messages(history)
    summary_tokens = (
        # Use HumanMessage and not SystemMessage because the Anthropic count_tokens API does not process
        # system messages.
        llm.get_num_tokens_from_messages([HumanMessage(content=summary)])
        if summary
        else INITIAL_SUMMARY_TOKENS_ESTIMATE
    )
    first_pass_done = False  # Ensures at least one iteration
    while not first_pass_done or _tokens_exceeds_limit(
        history, token_count=(history_tokens + summary_tokens + input_message_tokens), limit=max_token_limit
    ):
        first_pass_done = True
        # Keep pruning messages if token limit or message limit is exceeded
        while _tokens_exceeds_limit(
            history, token_count=(history_tokens + summary_tokens + input_message_tokens), limit=max_token_limit
        ) or _messages_exceeds_limit(history, input_messages):
            prune_count = 1
            if _messages_exceeds_limit(history, input_messages):
                prune_count = len(history) + len(input_messages) - MAX_UNCOMPRESSED_MESSAGES

            pruned_messages, history = history[:prune_count], history[prune_count:]
            pruned_memory.extend(pruned_messages)
            history_tokens = llm.get_num_tokens_from_messages(history)
        # Generate a new summary after pruning messages
        summary_token_limit = max_token_limit - history_tokens - input_message_tokens

        if summary := _get_new_summary(llm, pruned_memory, summary, model_token_limit=max_token_limit):
            summary_tokens = llm.get_num_tokens_from_messages([HumanMessage(content=summary)])
            if summary_tokens > summary_token_limit:
                summary, summary_token_limit = _reduce_summary_size(llm, summary, summary_token_limit)
        else:
            summary = ""
            summary_tokens = 0

    return history, pruned_memory, summary


def compress_chat_history_from_messages(
    llm,
    history,
    keep_history_len: int,
    max_token_limit: int,
    input_messages: list,
    history_mode: str = PipelineChatHistoryModes.SUMMARIZE,
):
    """
    Handles chat history compression based on selected mode:
    - "Summarize": Summarizes older messages when exceeding token limit.
    - "Truncate Tokens": Deletes old messages when exceeding token limit.
    - "Max History Length": Always keeps the last `keep_history_len` messages.
    """
    if history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        raise ValueError("History mode cannot be MAX_HISTORY_LENGTH for this function")

    summary = history.pop(0).content if history and history[0].type == ChatMessageType.SYSTEM else None
    input_message_tokens = llm.get_num_tokens_from_messages(input_messages)
    if history_mode == PipelineChatHistoryModes.TRUNCATE_TOKENS:
        history = truncate_tokens(history, max_token_limit, llm, input_message_tokens)
        return history, history[0] if history else None, COMPRESSION_MARKER

    # Default mode: PipelineChatHistoryModes.SUMMARIZE
    history, pruned_memory = history[-keep_history_len:], history[:-keep_history_len]
    latest_message = history[-1] if history else None
    try:
        history, pruned_memory, summary = summarize_history(
            llm, history, max_token_limit, input_message_tokens, summary, input_messages, pruned_memory
        )
        log.info(
            "Compressed chat history to %s tokens (%s prompt + %s summary + %s history)",
            input_message_tokens
            + llm.get_num_tokens_from_messages(history)
            + llm.get_num_tokens_from_messages([HumanMessage(content=summary)]),
            input_message_tokens,
            llm.get_num_tokens_from_messages([HumanMessage(content=summary)]),
            llm.get_num_tokens_from_messages(history),
        )
    except ChatException as e:
        log.exception("Error while compressing chat history: %s", e)
        pruned_memory = history[:]
        history = []
        summary = ""

    if history:
        last_message = history[0]
    elif pruned_memory:
        last_message = pruned_memory[-1]
    else:
        last_message = latest_message
    return history, last_message, summary


def _tokens_exceeds_limit(history, token_count, limit) -> bool:
    return history and token_count > limit


def _messages_exceeds_limit(history, input_messages) -> bool:
    return history and len(history) + len(input_messages) > MAX_UNCOMPRESSED_MESSAGES


def _get_new_summary(llm, pruned_memory, summary, model_token_limit, first_call=True):
    """Get a new summary from the pruned memory. If the pruned memory is still too long, prune it further and
    recursively call this function with the remaining memory."""
    summary = summary or ""

    if not pruned_memory:
        return summary
    if first_call:
        pruned_memory = [
            HumanMessage(
                " ".join(msg.content.split()[:1000]) + " ..." if len(msg.content.split()) > 1000 else msg.content
            )
            for msg in pruned_memory
            if msg.content
        ]

    summarization_prompt_tokens, context = _get_summarization_prompt_tokens_with_context(llm, summary, pruned_memory)
    next_batch = []

    while (
        pruned_memory
        and summarization_prompt_tokens > model_token_limit
        or len(pruned_memory) > MAX_UNCOMPRESSED_MESSAGES
    ):
        next_batch.insert(0, pruned_memory.pop())
        summarization_prompt_tokens, context = _get_summarization_prompt_tokens_with_context(
            llm, summary, pruned_memory
        )

    if not pruned_memory:
        if first_call:
            if summary:
                log.error(SUMMARY_TOO_LARGE_ERROR_MESSAGE)
            else:
                log.error(MESSAGES_TOO_LARGE_ERROR_MESSAGE.format(tokens=model_token_limit))
            return _get_new_summary(llm, next_batch, "", model_token_limit, False)
        else:
            raise ChatException("Unable to compress history")

    chain = (SUMMARY_PROMPT | llm).with_config({"run_name": "compress_chat_history"})
    summary = chain.invoke(context).text()

    if next_batch:
        return _get_new_summary(llm, next_batch, summary, model_token_limit, False)

    return summary


def _get_summarization_prompt_tokens_with_context(llm, summary, pruned_memory):
    new_lines = get_buffer_string(pruned_memory)
    context = {"summary": summary or "", "new_lines": new_lines}
    tokens = llm.get_num_tokens_from_messages(SUMMARY_PROMPT.format_prompt(**context).to_messages())
    return tokens, context


def _reduce_summary_size(llm, summary, summary_token_limit) -> tuple:
    if summary_token_limit <= 0:
        raise ChatException("Unable to compress history: summary token <= 0")
    summary_tokens = llm.get_num_tokens_from_messages([HumanMessage(content=summary)])
    attempts = 0
    while summary and summary_tokens > summary_token_limit:
        if attempts == 3:
            raise ChatException("Too many attempts trying to reduce summary size.")

        chain = (SUMMARY_COMPRESSION_PROMPT | llm).with_config({"run_name": "compress_chat_history"})
        summary = chain.invoke({"summary": summary}).text()
        summary_tokens = llm.get_num_tokens_from_messages([HumanMessage(content=summary)])
        attempts += 1

    return summary, summary_tokens
