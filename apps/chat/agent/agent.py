from datetime import datetime

import pytz
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain.chat_models.base import BaseChatModel
from langchain.schema import BaseMemory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.prompts.chat import MessageLike

from apps.chat.agent.tools import get_tools
from apps.experiments.models import ExperimentSession


def build_agent(
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
    tools = get_tools(experiment_session)
    # TODO: use https://python.langchain.com/docs/integrations/chat/anthropic_functions
    # when we implement this for anthropic
    agent = create_openai_tools_agent(llm=llm, tools=tools, prompt=agent_prompt)
    return AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        memory=memory,
        max_execution_time=120,
    )
