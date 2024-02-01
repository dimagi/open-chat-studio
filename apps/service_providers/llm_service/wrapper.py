from datetime import datetime
from operator import itemgetter
from typing import Any, Dict, List, Literal, Optional

import pytz
from langchain.agents import AgentExecutor, create_openai_tools_agent
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
from apps.experiments.models import Experiment, ExperimentSession


class ChainOutput(Serializable):
    output: str
    """String text."""
    prompt_tokens: int
    """Number of tokens in the prompt."""
    completion_tokens: int
    """Number of tokens in the completion."""

    type: Literal["OcsChainOutput"] = "OcsChainOutput"

    @classmethod
    def is_lc_serializable(cls) -> bool:
        """Return whether this class is serializable."""
        return True

    @classmethod
    def get_lc_namespace(cls) -> List[str]:
        """Get the namespace of the langchain object."""
        return ["ocs", "schema", "chain_output"]


class ExperimentRunnable(RunnableSerializable[Dict, ChainOutput]):
    experiment: Experiment
    session: ExperimentSession = None
    memory: BaseMemory = ConversationBufferMemory(return_messages=True)

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return False

    def invoke(self, input: dict, config: Optional[RunnableConfig] = None) -> ChainOutput:
        input = input.copy()
        input["source_material"] = self.source_material

        callback = self.callback_handler
        config = ensure_config(config)
        config["callbacks"] = config["callbacks"] or []
        config["callbacks"].append(callback)

        output = self.chain.invoke(input, config)
        return ChainOutput(
            output=output, prompt_tokens=callback.prompt_tokens, completion_tokens=callback.completion_tokens
        )

    @property
    def llm_service(self):
        return self.experiment.llm_provider.get_llm_service()

    @property
    def source_material(self):
        return self.experiment.source_material.material if self.experiment.source_material else ""

    @property
    def chain(self) -> Runnable[Dict[str, Any], str]:
        # if self.experiment.assistant:
        #     model = self.llm_service.get_assistant(self.experiment.assistant, as_agent=True)
        #     model |= itemgetter("output")
        # else:
        model = self.llm_service.get_chat_model(self.experiment.llm, self.experiment.temperature)

        if self.session and self.experiment.tools_enabled:
            tools = get_tools(self.session)
            # TODO: use https://python.langchain.com/docs/integrations/chat/anthropic_functions
            # when we implement this for anthropic
            agent = create_openai_tools_agent(llm=model, tools=tools, prompt=self.agent_prompt)
            executor = AgentExecutor.from_agent_and_tools(
                agent=agent,
                tools=tools,
                memory=self.memory,
                max_execution_time=120,
            )
            return executor | itemgetter("output")
        else:
            return (
                RunnablePassthrough.assign(
                    history=RunnableLambda(self.memory.load_memory_variables) | itemgetter("history")
                )
                | self.chat_prompt
                | model
                | StrOutputParser()
            )

    @property
    def callback_handler(self):
        model = self.llm_service.get_chat_model(self.experiment.llm, self.experiment.temperature)
        return self.llm_service.get_callback_handler(model)

    @property
    def chat_prompt(self):
        system_prompt = SystemMessagePromptTemplate.from_template(self.experiment.chatbot_prompt.prompt)
        return ChatPromptTemplate.from_messages(
            [
                system_prompt,
                MessagesPlaceholder("history", optional=True),
                ("human", "{input}"),
            ]
        )

    @property
    def agent_prompt(self):
        prompt = self.chat_prompt
        prompt.extend(
            [
                ("system", str(datetime.now().astimezone(pytz.UTC))),
                MessagesPlaceholder("agent_scratchpad"),
            ]
        )
        return prompt
