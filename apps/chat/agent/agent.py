from langchain.agents import AgentType, initialize_agent
from langchain.agents.openai_functions_agent.base import OpenAIFunctionsAgent
from langchain.chat_models.base import BaseLanguageModel
from langchain.memory import ConversationBufferMemory
from langchain.schema import BaseMemory

from apps.chat.agent.tools import tools
from apps.experiments.models import ExperimentSession


class AgentExecuter:
    """
    This wrapper manages an agent and provides it the context to operate in. Agents are able to use user defined
    tools when deemed necessary. In order to make user specific context available to these tools, this class keeps
    track of the session in which the agent is being executed.

    To learn more about LangChain agents, see the docs: https://docs.langchain.com/docs/components/agents
    """

    def __init__(self, llm: BaseLanguageModel, memory: ConversationBufferMemory, experiment_session: ExperimentSession):
        self._agent_executor = initialize_agent(
            tools=tools,
            llm=llm,
            agent=AgentType.OPENAI_FUNCTIONS,
            verbose=True,
            memory=memory,
            max_execution_time=120,
        )

        self.agent: OpenAIFunctionsAgent = self._agent_executor.agent
        for tool in self._agent_executor.tools:
            tool.experiment_session = experiment_session

    @property
    def memory(self) -> BaseMemory:
        return self._agent_executor.memory

    def predict(self, input: str):
        return self._agent_executor.run(input=input)
