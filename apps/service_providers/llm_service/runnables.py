from abc import ABC
from datetime import datetime
from operator import itemgetter
from typing import Any, Literal

import pytz
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.agents.openai_assistant.base import OpenAIAssistantFinish
from langchain.memory import ConversationBufferMemory
from langchain_core.load import Serializable
from langchain_core.memory import BaseMemory
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, SystemMessagePromptTemplate
from langchain_core.runnables import (
    Runnable,
    RunnableConfig,
    RunnableLambda,
    RunnablePassthrough,
    RunnableSerializable,
    ensure_config,
)

from apps.chat.agent.tools import get_tools
from apps.chat.conversation import compress_chat_history
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession


def create_experiment_runnable(experiment: Experiment, session: ExperimentSession) -> "BaseExperimentRunnable":
    """Create an experiment runnable based on the experiment configuration."""
    if experiment.assistant:
        return AssistantExperimentRunnable(experiment=experiment, session=session)

    assert experiment.llm, "Experiment must have an LLM model"
    assert experiment.llm_provider, "Experiment must have an LLM provider"
    if experiment.tools_enabled:
        return AgentExperimentRunnable(experiment=experiment, session=session)

    return SimpleExperimentRunnable(experiment=experiment, session=session)


class ChainOutput(Serializable):
    output: str
    """String text."""
    prompt_tokens: int
    """Number of tokens in the prompt."""
    completion_tokens: int
    """Number of tokens in the completion."""

    type: Literal["OcsChainOutput"] = "ChainOutput"

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """Return whether this class is serializable."""
        return True

    @classmethod
    def get_lc_namespace(cls) -> list[str]:
        """Get the namespace of the langchain object."""
        return ["ocs", "schema", "chain_output"]


class BaseExperimentRunnable(RunnableSerializable[dict, ChainOutput], ABC):
    experiment: Experiment
    session: ExperimentSession

    class Config:
        arbitrary_types_allowed = True

    @property
    def llm_service(self):
        return self.experiment.get_llm_service()

    @property
    def callback_handler(self):
        model = self.llm_service.get_chat_model(self.experiment.llm, self.experiment.temperature)
        return self.llm_service.get_callback_handler(model)

    def _save_message_to_history(self, message: str, type_: ChatMessageType):
        ChatMessage.objects.create(
            chat=self.session.chat,
            message_type=type_.value,
            content=message,
        )


class ExperimentRunnable(BaseExperimentRunnable):
    memory: BaseMemory = ConversationBufferMemory(return_messages=True)

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return False

    def invoke(self, input: str, config: RunnableConfig | None = None) -> ChainOutput:
        callback = self.callback_handler
        config = ensure_config(config)
        config["callbacks"] = config["callbacks"] or []
        config["callbacks"].append(callback)

        chain = self._build_chain()
        self._populate_memory()

        if config.get("configurable", {}).get("save_input_to_history", True):
            self._save_message_to_history(input, ChatMessageType.HUMAN)

        output = chain.invoke(input, config)

        self._save_message_to_history(output, ChatMessageType.AI)
        return ChainOutput(
            output=output, prompt_tokens=callback.prompt_tokens, completion_tokens=callback.completion_tokens
        )

    @property
    def source_material(self):
        return self.experiment.source_material.material if self.experiment.source_material else ""

    def _build_chain(self) -> Runnable[dict[str, Any], str]:
        raise NotImplementedError

    @property
    def prompt(self):
        system_prompt = SystemMessagePromptTemplate.from_template(self.experiment.prompt_text)
        return ChatPromptTemplate.from_messages(
            [
                system_prompt,
                MessagesPlaceholder("history", optional=True),
                ("human", "{input}"),
            ]
        )

    def format_input(self, input: dict):
        if self.experiment.input_formatter:
            input["input"] = self.experiment.input_formatter.format(input=input["input"])
        return input

    def _populate_memory(self):
        # TODO: convert to use BaseChatMessageHistory object
        model = self.llm_service.get_chat_model(self.experiment.llm, self.experiment.temperature)
        messages = compress_chat_history(self.session.chat, model, self.experiment.max_token_limit)
        self.memory.chat_memory.messages = messages

    def _save_message_to_history(self, message: str, type_: ChatMessageType):
        ChatMessage.objects.create(
            chat=self.session.chat,
            message_type=type_.value,
            content=message,
        )


class SimpleExperimentRunnable(ExperimentRunnable):
    def _build_chain(self) -> Runnable[dict[str, Any], str]:
        model = self.llm_service.get_chat_model(self.experiment.llm, self.experiment.temperature)
        return (
            {"input": RunnablePassthrough()}
            | RunnablePassthrough.assign(source_material=RunnableLambda(lambda x: self.source_material))
            | RunnablePassthrough.assign(
                history=RunnableLambda(self.memory.load_memory_variables) | itemgetter("history")
            )
            | RunnableLambda(self.format_input)
            | self.prompt
            | model
            | StrOutputParser()
        )


class AgentExperimentRunnable(ExperimentRunnable):
    def _build_chain(self) -> Runnable[dict[str, Any], str]:
        assert self.experiment.tools_enabled
        model = self.llm_service.get_chat_model(self.experiment.llm, self.experiment.temperature)
        tools = get_tools(self.session)
        # TODO: use https://python.langchain.com/docs/integrations/chat/anthropic_functions
        # when we implement this for anthropic
        agent = (
            RunnablePassthrough.assign(source_material=RunnableLambda(lambda x: self.source_material))
            | RunnableLambda(self.format_input)
            | create_openai_tools_agent(llm=model, tools=tools, prompt=self.prompt)
        )
        executor = AgentExecutor.from_agent_and_tools(
            agent=agent,
            tools=tools,
            memory=self.memory,
            max_execution_time=120,
        )
        return {"input": RunnablePassthrough()} | executor | itemgetter("output")

    @property
    def prompt(self):
        prompt = super().prompt
        return ChatPromptTemplate.from_messages(
            prompt.messages
            + [
                ("system", str(datetime.now().astimezone(pytz.UTC))),
                MessagesPlaceholder("agent_scratchpad"),
            ]
        )


class AssistantExperimentRunnable(BaseExperimentRunnable):
    class Config:
        arbitrary_types_allowed = True

    @property
    def chat(self):
        return self.session.chat

    def invoke(self, input: str, config: RunnableConfig | None = None) -> ChainOutput:
        callback = self.callback_handler
        config = ensure_config(config)
        config["callbacks"] = config["callbacks"] or []
        config["callbacks"].append(callback)

        assistant_runnable = self.experiment.assistant.get_assistant()

        input_dict = {"content": input}

        self._save_message_to_history(input, ChatMessageType.HUMAN)

        # Note: if this is not a new chat then the history won't be persisted to the thread
        thread_id = self.chat.get_metadata(self.chat.MetadataKeys.OPENAI_THREAD_ID)
        if thread_id:
            input_dict["thread_id"] = thread_id

        response: OpenAIAssistantFinish = assistant_runnable.invoke(input_dict, config)
        if not thread_id:
            self.chat.set_metadata(self.chat.MetadataKeys.OPENAI_THREAD_ID, response.thread_id)

        output = response.return_values["output"]
        self._save_message_to_history(output, ChatMessageType.AI)

        return ChainOutput(output=response.return_values["output"], prompt_tokens=0, completion_tokens=0)
