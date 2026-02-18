from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

import pydantic
from pydantic import BaseModel

from apps.help.agent import build_system_agent
from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent

_system_prompt = None


def _get_system_prompt():
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = (Path(__file__).parent.parent / "code_generate_system_prompt.md").read_text()
    return _system_prompt


class CodeGenerateInput(BaseModel):
    query: str
    context: str = ""


class CodeGenerateOutput(BaseModel):
    code: str


@register_agent
class CodeGenerateAgent(BaseHelpAgent[CodeGenerateInput, CodeGenerateOutput]):
    name: ClassVar[str] = "code_generate"
    mode: ClassVar[Literal["high", "low"]] = "high"
    max_retries: ClassVar[int] = 3

    @classmethod
    def get_user_message(cls, input: CodeGenerateInput) -> str:
        return input.query

    def run(self) -> CodeGenerateOutput:
        from apps.pipelines.nodes.nodes import DEFAULT_FUNCTION

        current_code = self.input.context
        if current_code == DEFAULT_FUNCTION:
            current_code = ""

        return self._run_with_retry(current_code, error=None, iteration=0)

    def _run_with_retry(self, current_code: str, error: str | None, iteration: int) -> CodeGenerateOutput:
        if iteration > self.max_retries:
            return CodeGenerateOutput(code=current_code)

        system_prompt = self._build_system_prompt(current_code, error)

        agent = build_system_agent(self.mode, system_prompt)
        response = agent.invoke({"messages": [{"role": "user", "content": self.get_user_message(self.input)}]})

        response_code = response["messages"][-1].text

        from apps.pipelines.nodes.nodes import CodeNode

        try:
            CodeNode.model_validate({"code": response_code, "name": "code", "node_id": "code", "django_node": None})
        except pydantic.ValidationError as e:
            return self._run_with_retry(response_code, error=str(e), iteration=iteration + 1)

        return CodeGenerateOutput(code=response_code)

    def _build_system_prompt(self, current_code: str, error: str | None) -> str:
        system_prompt = _get_system_prompt()
        prompt_context = {"current_code": "", "error": ""}

        if current_code:
            prompt_context["current_code"] = f"The current function definition is:\n\n{current_code}"
        if error:
            prompt_context["error"] = f"\nThe current function has the following error. Try to resolve it:\n\n{error}"

        system_prompt = system_prompt.format(**prompt_context).strip()
        system_prompt += (
            "\n\nIMPORTANT: Start your response with exactly"
            " `def main(input: str, **kwargs) -> str:` and nothing else before it."
        )
        return system_prompt
