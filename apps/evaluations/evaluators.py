from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict
from pydantic_core import ValidationError

from apps.evaluations.exceptions import EvaluationRunException
from apps.evaluations.field_definitions import FieldDefinition
from apps.evaluations.models import EvaluationMessage, EvaluationMessageContent
from apps.evaluations.utils import schema_to_pydantic_model
from apps.pipelines.nodes.base import UiSchema, Widgets
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.llm_service.default_models import get_model_parameters
from apps.service_providers.llm_service.main import LlmService
from apps.service_providers.llm_service.prompt_context import SafeAccessWrapper
from apps.service_providers.llm_service.retry import RATE_LIMIT_EXCEPTIONS
from apps.service_providers.models import LlmProviderModel
from apps.utils.python_execution import RestrictedPythonExecutionMixin, get_code_error_message


class EvaluatorSchema(BaseModel):
    label: str
    icon: str | None = None


class EvaluatorResult(BaseModel):
    message: dict | None  # A copy of the message this was run against
    generated_response: str  # The generated response from the bot
    result: dict | None  # The output from the evaluator


class BaseEvaluator(BaseModel):
    def run(self, message: EvaluationMessage, generated_response: str) -> EvaluatorResult:
        raise NotImplementedError


class LLMResponseMixin(BaseModel):
    llm_provider_id: int = Field(..., title="LLM Model", json_schema_extra=UiSchema(widget=Widgets.llm_provider_model))
    llm_provider_model_id: int = Field(..., json_schema_extra=UiSchema(widget=Widgets.none))
    llm_temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, title="Temperature", json_schema_extra=UiSchema(widget=Widgets.range)
    )

    def get_llm_service(self) -> LlmService:
        from apps.service_providers.models import LlmProvider

        try:
            provider = LlmProvider.objects.get(id=self.llm_provider_id)
            return provider.get_llm_service()
        except LlmProvider.DoesNotExist as err:
            raise EvaluationRunException("LLM Provider does not exist") from err
        except ServiceProviderConfigError as err:
            raise EvaluationRunException("There was an issue configuring the LLM service provider") from err

    def get_llm_provider_model(self):
        try:
            return LlmProviderModel.objects.get(id=self.llm_provider_model_id)
        except LlmProviderModel.DoesNotExist as err:
            raise EvaluationRunException("LLM Provider Model does not exist") from err

    def get_chat_model(self) -> BaseChatModel:
        model_name = self.get_llm_provider_model().name
        params = get_model_parameters(model_name, temperature=self.llm_temperature)
        return self.get_llm_service().get_chat_model(model_name, **params)


class LlmEvaluator(LLMResponseMixin, BaseEvaluator):
    model_config = ConfigDict(evaluator_schema=EvaluatorSchema(label="LLM Evaluator", icon="fa-robot"))  # ty: ignore[invalid-key]

    prompt: str = Field(
        description=(
            "The prompt template to use for evaluation. "
            "Available variables: {input.content}, {output.content}, {context.[context_parameter]}, {full_history} "
            "{generated_response}"
        ),
        json_schema_extra=UiSchema(widget=Widgets.text_editor),
    )
    output_schema: dict[str, FieldDefinition] = Field(
        description="The expected output schema for the evaluation",
        json_schema_extra=UiSchema(widget=Widgets.key_value_pairs),
    )

    def run(self, message: EvaluationMessage, generated_response: str) -> EvaluatorResult:
        # Create a pydantic class so the llm output is validated
        output_model = schema_to_pydantic_model(self.output_schema)
        llm = self.get_chat_model().with_structured_output(output_model)

        llm_with_retry = llm.with_retry(
            stop_after_attempt=3,
            retry_if_exception_type=(ValueError,) + RATE_LIMIT_EXCEPTIONS,
        )

        try:
            input = EvaluationMessageContent.model_validate(message.input)
        except ValidationError:
            input = {}

        try:
            output = EvaluationMessageContent.model_validate(message.output)
        except ValidationError:
            output = {}

        formatted_prompt = self.prompt.format(
            input=SafeAccessWrapper(input),
            output=SafeAccessWrapper(output),
            context=SafeAccessWrapper(message.context),
            full_history=message.full_history,
            generated_response=generated_response,
        )
        result_model = llm_with_retry.invoke(formatted_prompt)
        result = result_model.model_dump()
        return EvaluatorResult(message=message.as_result_dict(), generated_response=generated_response, result=result)


DEFAULT_FUNCTION = """# The main function is called for each message in the evaluation dataset

def main(input: dict, output: dict, context: dict, full_history: str, generated_response: str, **kwargs) -> dict:
    \"""Evaluate a single message and return metrics.

    Args:
        input: The input message data (e.g., {'content': 'Hello', 'role': 'human'})
        output: The actual output message / ground-truth data (e.g., {'content': 'Hello', 'role': 'ai'})
        context: Additional context of the message (e.g., {'current_datetime': '2025-06-02T18:51:55.334974+00:00'})
        full_history: Complete conversation history as a string (e.g., "user: hello!\nassistant: hello!\n")
        generated_response: The AI-generated response being evaluated, if enabled

    Returns:
        dict: Evaluation results where keys become columns in the output
              (e.g., {'accuracy': 0.95, 'relevance': 'high'})
    \"""
    return {'python_evaluation': input['content']}
"""


class PythonEvaluator(BaseEvaluator, RestrictedPythonExecutionMixin):
    """Runs python"""

    model_config = ConfigDict(
        evaluator_schema=EvaluatorSchema(  # ty: ignore[invalid-key]
            label="Python Evaluator",
            icon="fa-solid fa-file-code",
        )
    )
    code: str = Field(
        default=DEFAULT_FUNCTION,
        description="The code to run",
        json_schema_extra=UiSchema(widget=Widgets.code),
    )

    @classmethod
    def _get_default_code(cls) -> str:
        return DEFAULT_FUNCTION

    @classmethod
    def _get_function_args(cls) -> list[str]:
        return ["input", "output", "context", "full_history", "generated_response", "**kwargs"]

    def run(self, message: EvaluationMessage, generated_response: str) -> EvaluatorResult:
        try:
            input = EvaluationMessageContent.model_validate(message.input).model_dump()
        except ValidationError:
            input = {}

        try:
            output = EvaluationMessageContent.model_validate(message.output).model_dump()
        except ValidationError:
            output = {}

        try:
            result = self.compile_and_execute_code(
                input=input,
                output=output,
                context=message.context,
                full_history=message.full_history,
                generated_response=generated_response,
            )
            if not isinstance(result, dict):
                raise EvaluationRunException("The python function did not return a dictionary")
        except Exception as exc:
            raise EvaluationRunException(get_code_error_message("<inline_code>", self.code)) from exc

        return EvaluatorResult(message=message.as_result_dict(), generated_response=generated_response, result=result)
