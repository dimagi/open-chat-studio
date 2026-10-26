from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.config import ConfigDict
from pydantic_core import ValidationError

from apps.evaluations.const import (
    ALL_EVALUATOR_PROMPT_VARIABLE_ROOTS,
    GENERATION_PROMPT_VARIABLE_NAMES,
    SPAN_PROMPT_VARIABLE_NAMES,
    evaluator_prompt_variable_hints,
    prompt_variable_roots,
    unknown_generation_variables,
    unknown_span_variables,
)
from apps.evaluations.exceptions import EvaluationRunException
from apps.evaluations.field_definitions import FieldDefinition
from apps.evaluations.generation import (
    GenerationRecord,
    SpanType,
    judged_response,
    span_kwarg,
    span_prompt_variables,
)
from apps.evaluations.models import EvaluationMessage, EvaluationMessageContent
from apps.evaluations.usage import EvaluatorUsageContext, track_evaluator_usage
from apps.evaluations.utils import schema_to_pydantic_model
from apps.pipelines.nodes.base import UiSchema, Widgets
from apps.service_providers.exceptions import ServiceProviderConfigError
from apps.service_providers.llm_service.default_models import get_model_parameters
from apps.service_providers.llm_service.main import LlmService
from apps.service_providers.llm_service.prompt_context import SafeAccessWrapper
from apps.service_providers.llm_service.retry import rate_limit_exceptions
from apps.service_providers.llm_service.structured_output import structured_output_runnable
from apps.service_providers.models import LlmProvider, LlmProviderModel
from apps.utils.python_execution import RestrictedPythonExecutionMixin, get_code_error_message


class EvaluatorSchema(BaseModel):
    label: str
    icon: str | None = None


class EvaluatorResult(BaseModel):
    message: dict | None  # A copy of the message this was run against
    generated_response: str  # The generated response from the bot
    result: dict | None  # The output from the evaluator
    judged_response: str | None = None  # The selected span's output judged in place of the reply, if any


class BaseEvaluator(BaseModel):
    def run(
        self,
        message: EvaluationMessage,
        generated_response: str,
        *,
        usage_context: EvaluatorUsageContext | None = None,
        generation: GenerationRecord | None = None,
    ) -> EvaluatorResult:
        """Evaluate one message. `usage_context` attributes any LLM spend the
        evaluator incurs; evaluators that don't call an LLM ignore it. `generation`
        is what the bot generation did; an absent record reads as empty."""
        raise NotImplementedError


class SpanSelectorMixin(BaseModel):
    span_type: SpanType = Field(
        default="reply",
        title="Span",
        description="What the evaluator judges: the whole reply, or one node, tool call or LLM call of the generation",
    )
    span_name: str = Field(
        default="",
        title="Span name",
        description="The node name, tool name or LLM node name to select; leave empty for the whole reply",
    )
    span_as_response: bool = Field(
        default=False,
        title="Use the span's output as the generated response",
        description="Judge the selected span's output in place of the bot's reply",
        json_schema_extra=UiSchema(widget=Widgets.toggle),
    )

    @model_validator(mode="after")
    def selector_is_consistent(self):
        if self.span_type != "reply" and not self.span_name:
            raise ValueError("A span name is required when a node, tool or LLM span is selected")
        if self.span_type == "reply" and self.span_as_response:
            raise ValueError("Select a span to use its output as the generated response")
        return self

    def selected_spans(self, generation: GenerationRecord | None) -> list[dict]:
        if self.span_type == "reply":
            return []
        return (generation or GenerationRecord()).select_spans(self.span_type, self.span_name)


class LLMResponseMixin(BaseModel):
    # Supplied by ``Evaluator.get_evaluator_params`` from the
    # ``Evaluator.llm_provider``/``llm_provider_model`` FKs, which are where the selection is
    # stored. They are not part of ``params`` and are not rendered from this schema — the
    # evaluator form edits the FKs through its own fields (see ``LLM_PROVIDER_FIELDS``).
    llm_provider_id: int
    llm_provider_model_id: int
    llm_temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, title="Temperature", json_schema_extra=UiSchema(widget=Widgets.range)
    )

    def get_llm_service(self) -> LlmService:
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


# The ``LLMResponseMixin`` fields that are stored as FK columns on ``Evaluator`` instead of in
# ``params``. Kept out of the form-rendering schema and injected when the evaluator is built.
LLM_PROVIDER_FIELDS = ("llm_provider_id", "llm_provider_model_id")


class LlmEvaluator(SpanSelectorMixin, LLMResponseMixin, BaseEvaluator):
    model_config = ConfigDict(evaluator_schema=EvaluatorSchema(label="LLM Evaluator", icon="fa-robot"))  # ty: ignore[invalid-key]

    prompt: str = Field(
        description=(
            "The prompt template to use for evaluation. Available variables: "
            f"{', '.join(evaluator_prompt_variable_hints('message'))}"
        ),
        json_schema_extra=UiSchema(widget=Widgets.text_editor),
    )

    @field_validator("prompt")
    @classmethod
    def prompt_must_contain_variable(cls, v: str) -> str:
        # Roots only: an unknown sub-key still surfaces as an error result at run time.
        roots = prompt_variable_roots(v)
        if not roots & ALL_EVALUATOR_PROMPT_VARIABLE_ROOTS:
            raise ValueError(
                "The prompt must include at least one variable so the evaluator has context to work with. "
                f"Available variables: {', '.join(evaluator_prompt_variable_hints('message'))}"
            )
        # An unknown root would raise a KeyError from ``str.format`` when the evaluator runs.
        if unknown := sorted(roots - ALL_EVALUATOR_PROMPT_VARIABLE_ROOTS):
            raise ValueError(
                f"Unknown prompt variable(s): {', '.join(f'{{{name}}}' for name in unknown)}. "
                f"Available variables: {', '.join(evaluator_prompt_variable_hints('message'))}"
            )
        if unknown_generation := sorted(unknown_generation_variables(v)):
            raise ValueError(
                f"Unknown generation variable(s): {', '.join(f'{{{name}}}' for name in unknown_generation)}. "
                f"Available: {', '.join(f'{{{name}}}' for name in GENERATION_PROMPT_VARIABLE_NAMES)}"
            )
        if unknown_span := sorted(unknown_span_variables(v)):
            raise ValueError(
                f"Unknown span variable(s): {', '.join(f'{{{name}}}' for name in unknown_span)}. "
                f"Available: {', '.join(f'{{{name}}}' for name in SPAN_PROMPT_VARIABLE_NAMES)}"
            )
        return v

    output_schema: dict[str, FieldDefinition] = Field(
        description="The expected output schema for the evaluation",
        json_schema_extra=UiSchema(widget=Widgets.key_value_pairs),
    )

    @model_validator(mode="after")
    def span_variables_need_a_selector(self):
        if self.span_type == "reply" and "span" in prompt_variable_roots(self.prompt):
            raise ValueError("The prompt uses {span.input} or {span.output} but no span is selected")
        return self

    def run(
        self,
        message: EvaluationMessage,
        generated_response: str,
        *,
        usage_context: EvaluatorUsageContext | None = None,
        generation: GenerationRecord | None = None,
    ) -> EvaluatorResult:
        spans = self.selected_spans(generation)
        response = judged_response(spans, generated_response) if self.span_as_response else generated_response
        # Create a pydantic class so the llm output is validated
        output_model = schema_to_pydantic_model(self.output_schema)
        llm = structured_output_runnable(self.get_chat_model(), output_model)

        llm_with_retry = llm.with_retry(
            stop_after_attempt=3,
            retry_if_exception_type=(ValueError, *rate_limit_exceptions()),
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
            participant_data=SafeAccessWrapper(message.participant_data or {}),
            session_state=SafeAccessWrapper(message.session_state or {}),
            full_history=message.full_history,
            generated_response=response,
            generation=SafeAccessWrapper((generation or GenerationRecord()).prompt_variables()),
            span=SafeAccessWrapper(span_prompt_variables(spans)),
        )
        with track_evaluator_usage(usage_context) as callbacks:
            result_model = llm_with_retry.invoke(formatted_prompt, config={"callbacks": callbacks})
        result = result_model.model_dump()
        return EvaluatorResult(
            message=message.as_result_dict(),
            generated_response=generated_response,
            result=result,
            judged_response=response if self.span_as_response else None,
        )


DEFAULT_FUNCTION = """# The main function is called for each message in the evaluation dataset

def main(input: dict, output: dict, context: dict, full_history: str, generated_response: str, **kwargs) -> dict:
    \"""Evaluate a single message and return metrics.

    Args:
        input: The input message data (e.g., {'content': 'Hello', 'role': 'human'})
        output: The actual output message / ground-truth data (e.g., {'content': 'Hello', 'role': 'ai'})
        context: Additional context of the message (e.g., {'current_datetime': '2025-06-02T18:51:55.334974+00:00'})
        full_history: Complete conversation history as a string (e.g., "user: hello!\nassistant: hello!\n")
        generated_response: The AI-generated response being evaluated, if enabled
        **kwargs: Carries participant_data and session_state, both dicts captured at the time
              of the message (e.g. kwargs['session_state'].get('current_step')), and generation,
              a dict of what the bot generation did: node_outputs, path, message_tags, session_tags,
              participant_data_diff, llm_calls and tool_calls (e.g. 'urgent' in kwargs['generation']['message_tags']),
              and span, the selected node, tool or LLM span as a dict with type, node, name, input, output
              and error (a list when several match, None when no span is selected)

    Returns:
        dict: Evaluation results where keys become columns in the output
              (e.g., {'accuracy': 0.95, 'relevance': 'high'})
    \"""
    return {'python_evaluation': input['content']}
"""


class PythonEvaluator(SpanSelectorMixin, BaseEvaluator, RestrictedPythonExecutionMixin):
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

    def run(
        self,
        message: EvaluationMessage,
        generated_response: str,
        *,
        usage_context: EvaluatorUsageContext | None = None,
        generation: GenerationRecord | None = None,
    ) -> EvaluatorResult:
        try:
            input = EvaluationMessageContent.model_validate(message.input).model_dump()
        except ValidationError:
            input = {}

        try:
            output = EvaluationMessageContent.model_validate(message.output).model_dump()
        except ValidationError:
            output = {}

        spans = self.selected_spans(generation)
        response = judged_response(spans, generated_response) if self.span_as_response else generated_response

        try:
            result = self.compile_and_execute_code(
                input=input,
                output=output,
                context=message.context,
                full_history=message.full_history,
                generated_response=response,
                # Via **kwargs: _validate_function_signature demands an exact positional match,
                # so widening _get_function_args would invalidate every saved evaluator.
                participant_data=message.participant_data or {},
                session_state=message.session_state or {},
                generation=(generation or GenerationRecord()).as_dict(),
                span=span_kwarg(spans),
            )
            if not isinstance(result, dict):
                raise EvaluationRunException("The python function did not return a dictionary")
        except Exception as exc:
            raise EvaluationRunException(get_code_error_message("<inline_code>", self.code)) from exc

        return EvaluatorResult(
            message=message.as_result_dict(),
            generated_response=generated_response,
            result=result,
            judged_response=response if self.span_as_response else None,
        )
