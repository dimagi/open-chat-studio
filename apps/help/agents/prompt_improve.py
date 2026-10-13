from __future__ import annotations

import functools
from pathlib import Path
from typing import ClassVar, Literal

from django.forms import ValidationError
from pydantic import BaseModel, Field

from apps.help.agent import build_system_agent
from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent
from apps.utils.prompt import PROMPT_VAR_DESCRIPTIONS, PromptVars, get_prompt_variables


@functools.cache
def _get_system_prompt() -> str:
    return (Path(__file__).parent.parent / "prompt_improve_system_prompt.md").read_text()


class PromptImproveInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=50_000)
    node_type: Literal["llm", "router"] = "llm"
    tool_names: list[str] = []
    routes: list[str] = Field(default=[], description="A router node's routes, in output order")
    default_route: str = ""
    instruction: str = Field(default="", max_length=2_000)
    team_id: int | None = None


class PromptImproveOutput(BaseModel):
    prompt: str = Field(description="The full revised prompt")
    notes: list[str] = Field(description="One short sentence per change made")


def _known_variables(node_type: str) -> set[str]:
    """The variables a prompt on this node type may use."""
    return PromptVars.router_node_vars() if node_type == "router" else PromptVars.llm_node_vars()


def _names(variables: set[str]) -> str:
    return ", ".join(sorted(variables))


def _describe(variables: set[str]) -> str:
    return "\n".join(f"- {name}: {PROMPT_VAR_DESCRIPTIONS[name]}" for name in sorted(variables))


def _list_routes(routes: list[str], default_route: str) -> str:
    return "\n".join(f"- {route} (default)" if route == default_route else f"- {route}" for route in routes)


def _validation_error(original: str, rewrite: str, known_vars: set[str]) -> str | None:
    """Why `rewrite` cannot replace `original` on the node, or None if it can."""
    if not rewrite.strip():
        return "The rewrite is empty."
    try:
        rewrite_vars = get_prompt_variables(rewrite)
    except ValidationError as e:
        return "; ".join(e.messages)

    if unknown := rewrite_vars - known_vars:
        return f"The rewrite contains unknown variables: {_names(unknown)}"

    try:
        original_vars = get_prompt_variables(original)
    except ValidationError:
        original_vars = None
    if original_vars is not None:
        if missing := original_vars - rewrite_vars:
            return f"The rewrite must keep the variables: {_names(missing)}"
        if added := rewrite_vars - original_vars:
            return f"The rewrite must not add the variables: {_names(added)}"

    for variable in sorted(rewrite_vars):
        if rewrite.count(f"{{{variable}}}") > 1:
            return f"Variable {variable} is used more than once."
    return None


@register_agent
class PromptImproveAgent(BaseHelpAgent[PromptImproveInput, PromptImproveOutput]):
    name: ClassVar[str] = "prompt_improve"
    mode: ClassVar[Literal["high", "low"]] = "high"
    max_attempts: ClassVar[int] = 3

    @classmethod
    def get_system_prompt(cls, input: PromptImproveInput) -> str:
        return _get_system_prompt()

    @classmethod
    def get_user_message(cls, input: PromptImproveInput, error: str | None = None) -> str:
        sections = [
            f"<prompt>\n{input.prompt}\n</prompt>",
            f"<node_type>{input.node_type}</node_type>",
            f"<allowed_variables>\n{_describe(_known_variables(input.node_type))}\n</allowed_variables>",
            f"<enabled_tools>{', '.join(input.tool_names) or 'none'}</enabled_tools>",
        ]
        if input.node_type == "router" and input.routes:
            sections.append(f"<routes>\n{_list_routes(input.routes, input.default_route)}\n</routes>")
        if input.instruction:
            sections.append(f"<instruction>\n{input.instruction}\n</instruction>")
        if error:
            sections.append(
                f"<validation_error>\nYour previous rewrite was rejected: {error}\nFix this in your next rewrite."
                "\n</validation_error>"
            )
        return "\n\n".join(sections)

    def run(self) -> PromptImproveOutput:
        known_vars = _known_variables(self.input.node_type)
        error = None
        # One trace covers every attempt.
        with self._trace({"query": self.get_user_message(self.input)}) as trace_config:
            agent = build_system_agent(
                self.mode, self.get_system_prompt(self.input), response_format=PromptImproveOutput
            )
            for _attempt in range(self.max_attempts):
                response = agent.invoke(
                    {"messages": [{"role": "user", "content": self.get_user_message(self.input, error)}]},
                    config=trace_config,
                )
                output = self.parse_response(response)
                error = _validation_error(self.input.prompt, output.prompt, known_vars)
                if error is None:
                    return output
        return PromptImproveOutput(prompt=self.input.prompt, notes=[f"No valid rewrite was produced: {error}"])
