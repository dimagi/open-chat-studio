from typing import Optional, Tuple

from langchain.chains import ConversationChain
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

from apps.chat.agent.agent import build_agent
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
        llm: BaseChatModel,
        experiment_session: Optional[ExperimentSession] = None,
    ):
        self.llm = llm
        prompt_to_use = SystemMessagePromptTemplate.from_template(prompt_str)
        if source_material:
            try:
                prompt_to_use = prompt_to_use.format(source_material=source_material)
            except KeyError:
                # no source material found in prompt, just use it "naked"
                pass
        if experiment_session and experiment_session.experiment.tools_enabled:
            self.chain = build_agent(llm, memory, experiment_session, prompt_to_use)
        else:
            prompt = ChatPromptTemplate.from_messages(
                [
                    prompt_to_use,
                    MessagesPlaceholder(variable_name="history"),
                    HumanMessagePromptTemplate.from_template("{input}"),
                ]
            )

            # set output_key to match agent's output_key
            self.chain = ConversationChain(memory=memory, prompt=prompt, llm=llm, output_key="output")

    def load_memory(self, messages):
        self.chain.memory.chat_memory.messages = messages

    def predict(self, input: str) -> Tuple[str, int, int]:
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
