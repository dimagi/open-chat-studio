from functools import cached_property
from typing import Any

from langchain.chat_models.base import BaseChatModel
from langchain.prompts import PromptTemplate
from pydantic import model_validator

from apps.analysis import core
from apps.analysis.core import required


class LlmCompletionStepParams(core.Params):
    prompt: required(str) = None
    llm_model: required(str) = None

    @cached_property
    def prompt_template(self):
        if self.prompt:
            return PromptTemplate.from_template(self.prompt)

    @model_validator(mode="after")
    def check_prompt_inputs(self) -> "LlmCompletionStepParams":
        if self.prompt_template:
            input_variables = set(self.prompt_template.input_variables)
            if "data" not in input_variables:
                raise ValueError(f"Invalid prompt template. Prompts must have a 'data' variable.")
            elif len(input_variables) > 1:
                raise ValueError(f"Invalid prompt template. Prompts must only have a 'data' variable.")
        return self

    def get_form_class(self):
        from apps.analysis.steps.forms import LlmCompletionStepParamsForm

        return LlmCompletionStepParamsForm


class LlmCompletionStep(core.BaseStep[Any, str]):
    param_schema = LlmCompletionStepParams
    input_type = Any
    output_type = str

    def run(self, params: LlmCompletionStepParams, data: Any) -> tuple[str, dict]:
        llm: BaseChatModel = self.pipeline_context.llm_service.get_chat_model(params.llm_model, 1.0)
        prompt = params.prompt_template.format_prompt(data=data)
        result = llm.invoke(prompt)
        return result.content, {}
