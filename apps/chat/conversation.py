from typing import Optional, Tuple

from langchain import ConversationChain
from langchain.agents.openai_functions_agent.base import OpenAIFunctionsAgent
from langchain.callbacks import get_openai_callback
from langchain.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
)
from langchain.schema import BaseMemory

from apps.chat.agent.agent import AgentExecuter
from apps.experiments.models import ExperimentSession


class Conversation:
    """
    A wrapper class that provides a single way/API to interact with the LLMs, regardless of it being a normal
    conversation or agent implementation
    """

    def __init__(
        self,
        prompt_str: str,
        source_material: str,
        memory: BaseMemory,
        llm,
        experiment_session: Optional[ExperimentSession] = None,
    ):
        prompt_to_use = SystemMessagePromptTemplate.from_template(prompt_str)
        if source_material:
            try:
                prompt_to_use = prompt_to_use.format(source_material=source_material)
            except KeyError:
                # no source material found in prompt, just use it "naked"
                pass
        if experiment_session and experiment_session.experiment.tools_enabled:
            self.executer = AgentExecuter(llm=llm, memory=memory, experiment_session=experiment_session)
            # Insert the messages here
            self.executer.agent.prompt = OpenAIFunctionsAgent.create_prompt(
                system_message=prompt_to_use,
                extra_prompt_messages=[MessagesPlaceholder(variable_name="history")],
            )
        else:
            prompt = ChatPromptTemplate.from_messages(
                [
                    prompt_to_use,
                    MessagesPlaceholder(variable_name="history"),
                    HumanMessagePromptTemplate.from_template("{input}"),
                ]
            )
            self.executer = ConversationChain(memory=memory, prompt=prompt, llm=llm)

    @property
    def memory(self) -> BaseMemory:
        return self.executer.memory

    def predict(self, input: str) -> Tuple[str, str, str]:
        with get_openai_callback() as cb:
            response = self.executer.predict(input=input)
        return response, cb.prompt_tokens, cb.completion_tokens

    @property
    def _is_agent(self) -> bool:
        return isinstance(self.executer, AgentExecuter)
