import logging
from abc import ABC, abstractmethod

from langchain.chains import ConversationChain
from langchain.chains.llm import LLMChain
from langchain.memory.prompt import SUMMARY_PROMPT
from langchain.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
)
from langchain.schema import BaseMemory
from langchain_anthropic import ChatAnthropic
from langchain_community.callbacks import get_openai_callback
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage, get_buffer_string

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.pipelines.models import PipelineChatHistory, PipelineChatMessages

SUMMARY_TOO_LARGE_ERROR_MESSAGE = "Unable to compress chat history: existing summary too large"

INITIAL_SUMMARY_TOKENS_ESTIMATE = 20

log = logging.getLogger("ocs.bots")


class Conversation(ABC):
    @abstractmethod
    def predict(self, input: str) -> tuple[str, int, int]:
        raise NotImplementedError

    def load_memory_from_chat(self, chat: Chat, max_token_limit: int):
        pass

    def load_memory_from_messages(self, messages):
        pass


class BasicConversation(Conversation):
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
        prompt = ChatPromptTemplate.from_messages(
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
            try:
                prompt_to_use = prompt_to_use.format(source_material=self.source_material)
            except KeyError:
                # no source material found in prompt, just use it "naked"
                pass
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
) -> list[BaseMessage]:
    history_messages = chat.get_langchain_messages_until_summary()
    try:
        history, last_message, summary = _compress_chat_history(
            history=history_messages,
            llm=llm,
            max_token_limit=max_token_limit,
            input_messages=input_messages,
            keep_history_len=keep_history_len,
        )
        if summary is not None:
            ChatMessage.objects.filter(id=last_message.additional_kwargs["id"]).update(summary=summary)
            return [SystemMessage(content=summary)] + history
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
) -> list[BaseMessage]:
    history_messages = pipeline_chat_history.get_langchain_messages_until_summary()
    try:
        history, last_message, summary = _compress_chat_history(
            history=history_messages,
            llm=llm,
            max_token_limit=max_token_limit,
            input_messages=input_messages,
            keep_history_len=keep_history_len,
        )
        if summary is not None:
            PipelineChatMessages.objects.filter(id=last_message.additional_kwargs["id"]).update(summary=summary)
            return [SystemMessage(content=summary)] + history
        return history

    except (NameError, ImportError, ValueError, NotImplementedError):
        # typically this is because a library required to count tokens isn't installed
        log.exception("Unable to compress history")
        return history_messages


def _compress_chat_history(
    history: list, llm: BaseChatModel, max_token_limit: int, input_messages: list, keep_history_len: int = 10
) -> tuple[list[BaseMessage], BaseMessage | None, list[BaseMessage] | None]:
    """Compresses the chat history to be less than max_token_limit tokens long. This will summarize the history
    if necessary and save the summary to the DB.
    """
    if max_token_limit <= 0 or not history:
        log.info("Skipping chat history compression")
        return history, None, None

    total_messages = history.copy()
    total_messages.extend(input_messages)
    current_token_count = llm.get_num_tokens_from_messages(total_messages)
    if current_token_count <= max_token_limit:
        log.info("Skipping chat history compression: %s <= %s", current_token_count, max_token_limit)
        return history, None, None

    log.debug("Compressing chat history: current length %s > max %s", current_token_count, max_token_limit)

    history, last_message, summary = compress_chat_history_from_messages(
        llm, history, keep_history_len, max_token_limit, input_messages
    )
    return history, last_message, summary


def compress_chat_history_from_messages(
    llm, history, keep_history_len: int, max_token_limit: int, input_messages: list
):
    summary = history.pop(0).content if history[0].type == ChatMessageType.SYSTEM else None
    history, pruned_memory = history[-keep_history_len:], history[:-keep_history_len]

    summary_tokens = (
        llm.get_num_tokens_from_messages([SystemMessage(content=summary)])
        if summary
        else INITIAL_SUMMARY_TOKENS_ESTIMATE
    )
    input_message_tokens = llm.get_num_tokens_from_messages(input_messages)
    history_tokens = llm.get_num_tokens_from_messages(history)
    first_pass_done = False  # ensure we do at least one loop
    while not first_pass_done or (history and history_tokens + summary_tokens + input_message_tokens > max_token_limit):
        first_pass_done = True
        while history and history_tokens + summary_tokens + input_message_tokens > max_token_limit:
            pruned_memory.append(history.pop(0))
            history_tokens = llm.get_num_tokens_from_messages(history)

        summary = _get_new_summary(llm, pruned_memory, summary, max_token_limit)
        summary_tokens = llm.get_num_tokens_from_messages([SystemMessage(content=summary)])

    log.info(
        "Compressed chat history to %s tokens (%s prompt + %s summary + %s history)",
        input_message_tokens + history_tokens + summary_tokens,
        input_message_tokens,
        summary_tokens,
        history_tokens,
    )

    last_message = history[0] if history else pruned_memory[-1]
    return history, last_message, summary


def _get_new_summary(llm, pruned_memory, summary, max_token_limit):
    """Get a new summary from the pruned memory. If the prune memory is still too long, prune it further and
    recursively call this function with the remaining memory."""
    tokens, context = _get_summary_tokens_with_context(llm, summary, pruned_memory)
    next_batch = []
    while pruned_memory and tokens > max_token_limit:
        next_batch.insert(0, pruned_memory.pop())
        tokens, context = _get_summary_tokens_with_context(llm, summary, pruned_memory)

    if not context["new_lines"]:
        log.error(SUMMARY_TOO_LARGE_ERROR_MESSAGE)
        # If the summary is too large, discard it and compute a new summary from the pruned memory
        return _get_new_summary(llm, next_batch, None, max_token_limit)

    chain = LLMChain(llm=llm, prompt=SUMMARY_PROMPT, name="compress_chat_history")
    summary = chain.invoke(context)["text"]

    if next_batch:
        return _get_new_summary(llm, next_batch, summary, max_token_limit)

    return summary


def _get_summary_tokens_with_context(llm, summary, pruned_memory):
    new_lines = get_buffer_string(pruned_memory)
    context = {"summary": summary or "", "new_lines": new_lines}
    tokens = llm.get_num_tokens_from_messages(SUMMARY_PROMPT.format_prompt(**context).to_messages())
    return tokens, context
