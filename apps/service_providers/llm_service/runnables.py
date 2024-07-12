import functools
import logging
import re
import time
from operator import itemgetter
from time import sleep
from typing import Any, Literal

import openai
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain.agents.openai_assistant.base import OpenAIAssistantFinish
from langchain.memory import ConversationBufferMemory
from langchain_core.load import Serializable
from langchain_core.memory import BaseMemory
from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompt_values import PromptValue
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import (
    Runnable,
    RunnableConfig,
    RunnableLambda,
    RunnablePassthrough,
    RunnableSerializable,
    ensure_config,
)

from apps.chat.models import Chat, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.service_providers.llm_service.state import (
    AssistantExperimentState,
    AssistantState,
    ChatExperimentState,
    ChatRunnableState,
)

logger = logging.getLogger(__name__)


class GenerationError(Exception):
    pass


class GenerationCancelled(Exception):
    def __init__(self, output: "ChainOutput"):
        self.output = output


def create_experiment_runnable(experiment: Experiment, session: ExperimentSession):
    """Create an experiment runnable based on the experiment configuration."""
    if experiment.assistant:
        return AssistantExperimentRunnable(state=AssistantExperimentState(experiment=experiment, session=session))

    assert experiment.llm, "Experiment must have an LLM model"
    assert experiment.llm_provider, "Experiment must have an LLM provider"
    state = ChatExperimentState(experiment=experiment, session=session)
    if experiment.tools_enabled:
        return AgentExperimentRunnable(state=state)

    return SimpleExperimentRunnable(state=state)


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


class ExperimentRunnable(RunnableSerializable[str, ChainOutput]):
    state: ChatRunnableState
    memory: BaseMemory = ConversationBufferMemory(return_messages=True, output_key="output", input_key="input")
    cancelled: bool = False
    last_cancel_check: float | None = None
    check_every_ms: int = 1000
    input_key: str = "input"

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return False

    def invoke(self, input: str, config: RunnableConfig | None = None) -> ChainOutput:
        callback = self.state.callback_handler
        config = ensure_config(config)
        config["callbacks"] = config["callbacks"] or []
        config["callbacks"].append(callback)

        self._populate_memory(input)

        if config.get("configurable", {}).get("save_input_to_history", True):
            self.state.save_message_to_history(input, ChatMessageType.HUMAN)

        output = self._get_output_check_cancellation(input, config)
        result = ChainOutput(
            output=output, prompt_tokens=callback.prompt_tokens, completion_tokens=callback.completion_tokens
        )
        if self.cancelled:
            raise GenerationCancelled(result)

        if config.get("configurable", {}).get("save_output_to_history", True):
            experiment_tag = config.get("configurable", {}).get("experiment_tag")
            self.state.save_message_to_history(output, ChatMessageType.AI, experiment_tag)
        return result

    def _get_output_check_cancellation(self, input, config):
        chain = self._build_chain()

        output = ""
        for token in chain.stream(input, config):
            output += self._parse_output(token)
            if self._chat_is_cancelled():
                return output
        return output

    def _parse_output(self, output):
        return output

    def _chat_is_cancelled(self):
        if self.cancelled:
            return True

        if self.last_cancel_check and self.check_every_ms:
            if self.last_cancel_check + self.check_every_ms > time.time():
                return False

        self.last_cancel_check = time.time()

        self.cancelled = self.state.check_cancellation()
        return self.cancelled

    def _build_chain(self) -> Runnable[dict[str, Any], Any]:
        raise NotImplementedError

    @property
    def prompt(self):
        return ChatPromptTemplate.from_messages(
            [
                ("system", self.state.get_prompt()),
                MessagesPlaceholder("history", optional=True),
                ("human", "{input}"),
            ]
        )

    def _populate_memory(self, input: str):
        input_messages = self.get_input_messages(input)
        self.memory.chat_memory.messages = self.state.get_chat_history(input_messages)

    def get_input_messages(self, input: str) -> list[BaseMessage]:
        """Return a list of messages which represent the fully populated LLM input.
        This will be used during history compression.
        """
        raise NotImplementedError


class SimpleExperimentRunnable(ExperimentRunnable):
    def get_input_messages(self, input: str):
        chain = self._input_chain()
        return chain.invoke(input).to_messages()

    def _build_chain(self):
        return self._input_chain() | self.state.get_chat_model() | StrOutputParser()

    def _input_chain(self) -> Runnable[str, PromptValue]:
        source_material = RunnableLambda(lambda x: self.state.get_source_material())
        participant_data = RunnableLambda(lambda x: self.state.get_participant_data())
        current_datetime = RunnableLambda(lambda x: self.state.get_current_datetime())
        return (
            {"input": RunnablePassthrough()}
            | RunnablePassthrough.assign(source_material=source_material)
            | RunnablePassthrough.assign(participant_data=participant_data)
            | RunnablePassthrough.assign(current_datetime=current_datetime)
            | RunnablePassthrough.assign(
                history=RunnableLambda(self.memory.load_memory_variables) | itemgetter("history")
            )
            | RunnableLambda(functools.partial(self.state.format_input, self.input_key))
            | self.prompt
        )


class AgentExperimentRunnable(ExperimentRunnable):
    def _parse_output(self, output):
        return output.get("output", "")

    def _input_chain(self) -> Runnable[dict[str, Any], dict]:
        source_material = RunnableLambda(lambda x: self.state.get_source_material())
        participant_data = RunnableLambda(lambda x: self.state.get_participant_data())
        current_datetime = RunnableLambda(lambda x: self.state.get_current_datetime())
        return (
            RunnablePassthrough.assign(source_material=source_material)
            | RunnablePassthrough.assign(participant_data=participant_data)
            | RunnablePassthrough.assign(current_datetime=current_datetime)
            | RunnableLambda(functools.partial(self.state.format_input, self.input_key))
        )

    def _build_chain(self):
        tools = self.state.get_tools()
        agent = self._input_chain() | create_tool_calling_agent(
            llm=self.state.get_chat_model(), tools=tools, prompt=self.prompt
        )
        executor = AgentExecutor.from_agent_and_tools(
            agent=agent,
            tools=tools,
            memory=self.memory,
            max_execution_time=120,
        )
        return {"input": RunnablePassthrough()} | executor

    @property
    def prompt(self):
        prompt = super().prompt
        return ChatPromptTemplate.from_messages(prompt.messages + [MessagesPlaceholder("agent_scratchpad")])

    def get_input_messages(self, input: str):
        chain = (
            self._input_chain()
            # Since it's hard to guess what the agent_scratchpad will look like, let's just assume its empty
            | RunnablePassthrough.assign(agent_scratchpad=lambda x: [])
            | self.prompt
        )
        chain = {"input": RunnablePassthrough()} | chain
        return chain.invoke(input).to_messages()


class AssistantExperimentRunnable(RunnableSerializable[dict, ChainOutput]):
    state: AssistantState
    input_key = "content"

    class Config:
        arbitrary_types_allowed = True

    def invoke(self, input: str, config: RunnableConfig | None = None) -> ChainOutput:
        callback = self.state.callback_handler
        config = ensure_config(config)
        config["callbacks"] = config["callbacks"] or []
        config["callbacks"].append(callback)

        input_dict = {"content": input}

        if config.get("configurable", {}).get("save_input_to_history", True):
            self.state.save_message_to_history(input, ChatMessageType.HUMAN)

        # Note: if this is not a new chat then the history won't be persisted to the thread
        thread_id = self.state.get_metadata(Chat.MetadataKeys.OPENAI_THREAD_ID)
        if thread_id:
            input_dict["thread_id"] = thread_id

        input_dict["instructions"] = self.state.get_assistant_instructions()

        response = self._get_response_with_retries(config, input_dict, thread_id)
        if not thread_id:
            self.state.set_metadata(Chat.MetadataKeys.OPENAI_THREAD_ID, response.thread_id)

        output = response.return_values["output"]
        self.state.save_message_to_history(output, ChatMessageType.AI)

        return ChainOutput(output=response.return_values["output"], prompt_tokens=0, completion_tokens=0)

    def _get_response_with_retries(self, config, input_dict, thread_id):
        assistant = self.state.get_openai_assistant()
        format_input = functools.partial(self.state.format_input, self.input_key)
        assistant_runnable = RunnableLambda(format_input) | assistant
        for i in range(3):
            try:
                response: OpenAIAssistantFinish = assistant_runnable.invoke(input_dict, config)
            except openai.BadRequestError as e:
                self._handle_api_error(thread_id, assistant, e)
            except ValueError as e:
                if re.search(r"cancelling|cancelled", str(e)):
                    raise GenerationCancelled(ChainOutput(output="", prompt_tokens=0, completion_tokens=0))
            else:
                return response
        raise GenerationError("Failed to get response after 3 retries")

    def _handle_api_error(self, thread_id, assistant, exc):
        """Handle OpenAI API errors.
        This should either raise an exception or return if the error was handled and the run should be retried.
        """
        message = exc.body.get("message") or ""
        match = re.match(r".*(thread_[\w]+) while a run (run_[\w]+) is active.*", message)
        if not match:
            raise exc

        error_thread_id, run_id = match.groups()
        if error_thread_id != thread_id:
            raise GenerationError(f"Thread ID mismatch: {error_thread_id} != {thread_id}", exc)

        self._cancel_run(assistant, thread_id, run_id)

    def _cancel_run(self, assistant, thread_id, run_id):
        logger.info("Cancelling run %s in thread %s", run_id, thread_id)
        run = assistant.client.beta.threads.runs.cancel(thread_id=thread_id, run_id=run_id)
        cancelling = run.status == "cancelling"
        while cancelling:
            run = assistant.client.beta.threads.runs.retrieve(run_id, thread_id=thread_id)
            cancelling = run.status == "cancelling"
            if cancelling:
                sleep(assistant.check_every_ms / 1000)
