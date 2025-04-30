import contextlib
import logging

from langchain.chains.conversation.base import ConversationChain
from langchain.chains.llm import LLMChain
from langchain.memory.prompt import SUMMARY_PROMPT
from langchain_anthropic import ChatAnthropic
from langchain_community.callbacks import get_openai_callback
from langchain_core.language_models import BaseChatModel
from langchain_core.memory import BaseMemory
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, get_buffer_string
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
        memory: BaseMemory,
        llm: BaseChatModel,
    ):
        self.prompt_str = prompt_str
        self.source_material = source_material
        self.memory = memory
        self.llm = llm
        self._build_chain()

    def _build_chain(self):
        prompt = OcsPromptTemplate.from_messages(
            [
                self.system_prompt,
                MessagesPlaceholder(variable_name="history"),
                HumanMessagePromptTemplate.from_template("{input}"),
            ]
        )

        # set output_key to match agent's output_key
        self.chain = ConversationChain(memory=self.memory, prompt=prompt, llm=self.llm, output_key="output")

    @property
    def system_prompt(self):
        prompt_to_use = SystemMessagePromptTemplate.from_template(self.prompt_str)
        if self.source_material:
            with contextlib.suppress(KeyError):
                prompt_to_use = prompt_to_use.format(source_material=self.source_material)
        return prompt_to_use

    def load_memory_from_messages(self, messages: list[BaseMessage]):
        self.memory.chat_memory.messages = messages

    def load_memory_from_chat(self, chat: Chat, max_token_limit: int):
        self.load_memory_from_messages(self._get_optimized_history(chat, max_token_limit))

    def _get_optimized_history(self, chat: Chat, max_token_limit: int) -> list[BaseMessage]:
        return compress_chat_history(chat, self.llm, max_token_limit, input_messages=[])

    def predict(self, input: str) -> tuple[str, int, int]:
        if isinstance(self.llm, ChatAnthropic):
            # Langchain has no inbuilt functionality to return prompt or
            # completion tokens for Anthropic's models
            # https://python.langchain.com/docs/modules/model_io/llms/token_usage_tracking
            # Instead, we convert the prompt to a string, and count the tokens
            # with Anthropic's token counter.
            # TODO: When we enable the AgentExecuter for Anthropic models, we should revisit this
            response = self.chain.invoke({"input": input})
            prompt_tokens = self.llm.get_num_tokens_from_messages(response["history"][:-1])
            completion_tokens = self.llm.get_num_tokens_from_messages([response["history"][-1]])
            return response["output"], prompt_tokens, completion_tokens
        else:
            with get_openai_callback() as cb:
                response = self.chain.invoke({"input": input})
            output = response["output"]
            return output, cb.prompt_tokens, cb.completion_tokens


def compress_chat_history(
    chat: Chat,
    llm: BaseChatModel,
    max_token_limit: int,
    input_messages: list,
    keep_history_len: int = 10,
    history_mode: str = PipelineChatHistoryModes.SUMMARIZE,
) -> list[BaseMessage]:
    history_messages = chat.get_langchain_messages_until_summary()
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
                ChatMessage.objects.filter(id=last_message.additional_kwargs["id"]).update(summary=summary)
                return [SystemMessage(content=summary)] + history
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
    history_messages = pipeline_chat_history.get_langchain_messages_until_summary()
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
                PipelineChatMessages.objects.filter(id=last_message.additional_kwargs["id"]).update(summary=summary)
                return [SystemMessage(content=summary)] + history
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
    if max_token_limit <= 0 or not history:
        log.info("Skipping chat history compression")
        return history, None, None

    total_messages = history.copy()
    total_messages.extend(input_messages)
    current_token_count = llm.get_num_tokens_from_messages(total_messages)
    if history_mode in [PipelineChatHistoryModes.SUMMARIZE, PipelineChatHistoryModes.TRUNCATE_TOKENS, None]:
        if current_token_count <= max_token_limit and len(total_messages) <= MAX_UNCOMPRESSED_MESSAGES:
            log.info("Skipping chat history compression: %s <= %s", current_token_count, max_token_limit)
            return history, None, None
    elif history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        if keep_history_len is not None and len(total_messages) <= keep_history_len:
            log.info("Skipping chat history compression: %d <= %d", len(total_messages), keep_history_len)
            return history, None, None

    log.debug(
        "Compressing chat history with mode %s: %s/%s(max) tokens and %d/%d(max) messages",
        history_mode,
        current_token_count,
        max_token_limit,
        len(total_messages),
        keep_history_len,
    )
    history, last_message, summary = compress_chat_history_from_messages(
        llm, history, keep_history_len, max_token_limit, input_messages, history_mode
    )
    return history, last_message, summary


def truncate_tokens(history, max_token_limit, llm, input_message_tokens):
    """Removes old messages until the token count is below the max limit."""
    pruned_memory = []
    history_tokens = llm.get_num_tokens_from_messages(history)
    while history and history_tokens + input_message_tokens > max_token_limit:
        pruned_memory.append(history.pop(0))
        history_tokens = llm.get_num_tokens_from_messages(history)
    return history, pruned_memory


def summarize_history(llm, history, max_token_limit, input_message_tokens, summary, input_messages, pruned_memory):
    history_tokens = llm.get_num_tokens_from_messages(history)
    summary_tokens = (
        llm.get_num_tokens_from_messages([SystemMessage(content=summary)])
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
        try:
            summary = _get_new_summary(llm, pruned_memory, summary, model_token_limit=max_token_limit)
            summary_tokens = llm.get_num_tokens_from_messages([SystemMessage(content=summary)])
            if summary and summary_tokens > summary_token_limit:
                summary, summary_token_limit = _reduce_summary_size(llm, summary, summary_token_limit)
        except ChatException as e:
            log.exception("Error while generating summary: %s", e)
            summary = ""
            break

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
    summary = history.pop(0).content if history and history[0].type == ChatMessageType.SYSTEM else None
    history, pruned_memory = history[-keep_history_len:], history[:-keep_history_len]
    latest_message = history[-1] if history else None
    input_message_tokens = llm.get_num_tokens_from_messages(input_messages)
    if history_mode == PipelineChatHistoryModes.MAX_HISTORY_LENGTH:
        return history, latest_message, summary
    elif history_mode == PipelineChatHistoryModes.TRUNCATE_TOKENS:
        history, pruned_memory = truncate_tokens(history, max_token_limit, llm, input_message_tokens)
    elif history_mode == PipelineChatHistoryModes.SUMMARIZE or history_mode is None:
        history, pruned_memory, summary = summarize_history(
            llm, history, max_token_limit, input_message_tokens, summary, input_messages, pruned_memory
        )
        log.info(
            "Compressed chat history to %s tokens (%s prompt + %s summary + %s history)",
            input_message_tokens
            + llm.get_num_tokens_from_messages(history)
            + llm.get_num_tokens_from_messages([SystemMessage(content=summary)]),
            input_message_tokens,
            llm.get_num_tokens_from_messages([SystemMessage(content=summary)]),
            llm.get_num_tokens_from_messages(history),
        )
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

    chain = LLMChain(llm=llm, prompt=SUMMARY_PROMPT, name="compress_chat_history")
    summary = chain.invoke(context)["text"]

    if next_batch:
        return _get_new_summary(llm, next_batch, summary, model_token_limit, False)

    return summary


def _get_summarization_prompt_tokens_with_context(llm, summary, pruned_memory):
    new_lines = get_buffer_string(pruned_memory)
    context = {"summary": summary or "", "new_lines": new_lines}
    tokens = llm.get_num_tokens_from_messages(SUMMARY_PROMPT.format_prompt(**context).to_messages())
    return tokens, context


def _reduce_summary_size(llm, summary, summary_token_limit) -> tuple:
    summary_tokens = llm.get_num_tokens_from_messages([SystemMessage(content=summary)])
    attempts = 0
    while summary and summary_tokens > summary_token_limit:
        if attempts == 3:
            log.exception("Too many attempts trying to reduce summary size.")
            return "", 0
        chain = LLMChain(llm=llm, prompt=SUMMARY_COMPRESSION_PROMPT, name="compress_chat_history")
        summary = chain.invoke({"summary": summary})["text"]
        summary_tokens = llm.get_num_tokens_from_messages([SystemMessage(content=summary)])
        attempts += 1

    return summary, summary_tokens
