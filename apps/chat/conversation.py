import logging
from abc import ABC, abstractmethod

from langchain.chains import ConversationChain
from langchain.memory.summary import SummarizerMixin
from langchain.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
)
from langchain.schema import BaseMemory
from langchain_community.callbacks import get_openai_callback
from langchain_community.chat_models import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage

from apps.chat.models import Chat, ChatMessage, ChatMessageType

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

    def load_memory_from_chat(self, chat, max_token_limit):
        self.load_memory_from_messages(self._get_optimized_history(chat, max_token_limit))

    def _get_optimized_history(self, chat, max_token_limit) -> list[BaseMessage]:
        try:
            return compress_chat_history(chat, self.llm, max_token_limit)
        except (NameError, ImportError, ValueError, NotImplementedError):
            # typically this is because a library required to count tokens isn't installed
            log.exception("Unable to compress history")
            return chat.get_langchain_messages_until_summary()

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
    chat: Chat, llm: BaseChatModel, max_token_limit: int, keep_history_len: int = 10
) -> list[BaseMessage]:
    """Compresses the chat history to be less than max_token_limit tokens long. This will summarize the history
    if necessary and save the summary to the DB.
    """
    history = chat.get_langchain_messages_until_summary()
    if max_token_limit <= 0 or not history:
        return history

    current_token_count = llm.get_num_tokens_from_messages(history)
    if current_token_count <= max_token_limit:
        return history

    log.debug(
        "Compressing chat history to be less than %s tokens long. Current length: %s",
        max_token_limit,
        current_token_count,
    )
    summary = history.pop(0).content if history[0].type == ChatMessageType.SYSTEM else None
    history, pruned_memory = history[-keep_history_len:], history[:-keep_history_len]

    while llm.get_num_tokens_from_messages(history) > max_token_limit:
        pruned_memory.append(history.pop(0))

    summary = SummarizerMixin(llm=llm).predict_new_summary(pruned_memory, summary)
    ChatMessage.objects.filter(id=history[0].additional_kwargs["id"]).update(summary=summary)
    return [SystemMessage(content=summary)] + history
