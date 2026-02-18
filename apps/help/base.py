from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel

from apps.help.agent import build_system_agent


class BaseHelpAgent[TInput: BaseModel, TOutput: BaseModel](BaseModel):
    """Base class for help agents.

    Subclasses must define:
    - name: ClassVar[str] — registry key and URL slug
    - mode: ClassVar[Literal["high", "low"]] — model tier

    Subclasses that use the default run() must also define:
    - get_system_prompt(input) — build the system prompt
    - get_user_message(input) — build the user message
    - parse_response(response) — extract TOutput from agent response

    Subclasses may override run() entirely for custom execution logic.
    """

    name: ClassVar[str]
    mode: ClassVar[Literal["high", "low"]]

    input: TInput

    @classmethod
    def get_system_prompt(cls, input: TInput) -> str:
        raise NotImplementedError

    @classmethod
    def get_user_message(cls, input: TInput) -> str:
        raise NotImplementedError

    def run(self) -> TOutput:
        agent = build_system_agent(self.mode, self.get_system_prompt(self.input))
        response = agent.invoke({"messages": [{"role": "user", "content": self.get_user_message(self.input)}]})
        return self.parse_response(response)

    def parse_response(self, response) -> TOutput:
        raise NotImplementedError
