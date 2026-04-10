from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel

from apps.custom_actions.schema_utils import resolve_references
from apps.help.agent import build_system_agent


class BaseHelpAgent[TInput: BaseModel, TOutput: BaseModel](BaseModel):
    """Base class for help agents.

    Subclasses must define:
    - name: ClassVar[str] — registry key and URL slug
    - mode: ClassVar[Literal["high", "low"]] — model tier

    Subclasses that use the default run() must also define:
    - get_system_prompt(input) — build the system prompt
    - get_user_message(input) — build the user message

    Subclasses may optionally override:
    - parse_response(response) — custom response extraction (default: return structured_response)
    - run() — entirely custom execution logic

    The default run() passes TOutput as response_format for structured output.
    """

    name: ClassVar[str]
    mode: ClassVar[Literal["high", "low"]]

    input: TInput

    @classmethod
    def _get_output_type(cls) -> type[BaseModel]:
        """Resolve the concrete TOutput type from the class hierarchy."""
        for klass in cls.__mro__:
            meta = getattr(klass, "__pydantic_generic_metadata__", None)
            if meta and meta.get("origin") is BaseHelpAgent:
                return meta["args"][1]
        raise TypeError(f"Cannot determine output type for {cls.__name__}")

    @classmethod
    def _get_output_schema(cls) -> dict:
        """Convert the output model to a properly formatted JSON schema for structured output."""
        output_type = cls._get_output_type()
        schema = output_type.model_json_schema()
        # Resolve any references in the schema
        schema = resolve_references(schema)
        # Remove unnecessary keys
        schema.pop("$defs", None)
        return schema

    @classmethod
    def get_system_prompt(cls, input: TInput) -> str:
        raise NotImplementedError

    @classmethod
    def get_user_message(cls, input: TInput) -> str:
        raise NotImplementedError

    def run(self) -> TOutput:
        agent = build_system_agent(
            self.mode,
            self.get_system_prompt(self.input),
            response_format=self._get_output_schema(),
        )
        response = agent.invoke({"messages": [{"role": "user", "content": self.get_user_message(self.input)}]})
        return self.parse_response(response)

    def parse_response(self, response) -> TOutput:
        return response["structured_response"]
