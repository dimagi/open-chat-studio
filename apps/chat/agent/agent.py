from datetime import datetime

import pytz
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.agents.openai_functions_agent.base import OpenAIFunctionsAgent
from langchain.chat_models.base import BaseChatModel
from langchain.schema import BaseMemory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.prompts.chat import MessageLike

from apps.chat.agent.tools import get_tools
from apps.experiments.models import ExperimentSession


class AgentExecuter:
    """
    This wrapper manages an agent and provides it the context to operate in. Agents are able to use user defined
    tools when deemed necessary. In order to make user specific context available to these tools, this class keeps
    track of the session in which the agent is being executed.

    To learn more about LangChain agents, see the docs: https://docs.langchain.com/docs/components/agents

    # TODO: use
    https://python.langchain.com/docs/integrations/chat/anthropic_functions
    when we implement this for anthropic

    """

    def __init__(
        self,
        llm: BaseChatModel,
        memory: BaseMemory,
        experiment_session: ExperimentSession,
        prompt: MessageLike,
    ):
        agent_prompt = ChatPromptTemplate.from_messages(
            [
                prompt,
                MessagesPlaceholder("history", optional=True),
                ("system", str(datetime.now().astimezone(pytz.UTC))),
                ("human", "{input}"),
                MessagesPlaceholder("agent_scratchpad"),
            ]
        )
        agent = create_openai_tools_agent(llm=llm, tools=get_tools(), prompt=agent_prompt)
        self._agent_executor = AgentExecutor.from_agent_and_tools(
            agent=agent,
            tools=get_tools(),
            memory=memory,
            max_execution_time=120,
        )

        self.agent: OpenAIFunctionsAgent = self._agent_executor.agent
        for tool in self._agent_executor.tools:
            tool.experiment_session = experiment_session

    @property
    def memory(self) -> BaseMemory:
        return self._agent_executor.memory

    def invoke(self, input: dict[str], **kwargs):
        return self._agent_executor.invoke(input, **kwargs)
