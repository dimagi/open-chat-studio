from django.db.models import TextChoices
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from apps.custom_actions.schema_utils import resolve_references
from apps.pipelines.nodes.base import UiSchema, Widgets


class OpenAIReasoningEffortParameter(TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class GPT5ReasoningEffortParameter(TextChoices):
    MINIMAL = "minimal", "Minimal"
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class OpenAIVerbosityParameter(TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class OpenAIReasoningSummaryParameter(TextChoices):
    AUTO = "auto", "Auto"
    DETAILED = "detailed", "Detailed"


class LLMModelParamBase(BaseModel):
    pass


def _validate_token_limit(value: int, info, strictly_less_than: bool = False) -> int:
    value = int(value)
    model_max_token_limit = info.context.get("model_max_token_limit")
    if strictly_less_than and value >= model_max_token_limit:
        raise PydanticCustomError(
            "invalid_model_parameters",
            f"Tokens must be less than the model's max token limit of {model_max_token_limit}",
        )
    elif value > model_max_token_limit:
        raise PydanticCustomError(
            "invalid_model_parameters",
            f"Tokens cannot be more than the model's max token limit of {model_max_token_limit}",
        )
    return value


class OpenAIReasoningParameters(LLMModelParamBase):
    effort: OpenAIReasoningEffortParameter = Field(
        title="Reasoning Effort",
        default="low",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIReasoningEffortParameter.labels),
    )


class GPT5Parameters(LLMModelParamBase):
    effort: GPT5ReasoningEffortParameter = Field(
        title="Reasoning Effort",
        default="low",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=GPT5ReasoningEffortParameter.labels),
    )

    verbosity: OpenAIVerbosityParameter = Field(
        title="Verbosity",
        default="low",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIVerbosityParameter.labels),
    )


class GPT5ProParameters(LLMModelParamBase):
    verbosity: OpenAIVerbosityParameter = Field(
        title="Verbosity",
        default="low",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIVerbosityParameter.labels),
    )


class AnthropicBaseParameters(LLMModelParamBase):
    max_tokens: int = Field(
        title="Max Output Tokens",
        default=32000,
        description="The maximum number of tokens to generate in the completion.",
        ge=1,
    )

    @field_validator("max_tokens", mode="before")
    def ensure_value_is_less_than_model_max(cls, value: int, info):
        return _validate_token_limit(value, info, strictly_less_than=True)


class ClaudeHaikuLatestParameters(AnthropicBaseParameters):
    max_tokens: int = Field(
        title="Max Output Tokens",
        default=8192,
        description="The maximum number of tokens to generate in the completion.",
        ge=1,
        le=8192,
    )


class AnthropicNonReasoningParameters(AnthropicBaseParameters):
    top_k: int = Field(
        title="Top K",
        description="Only sample from the top K options for each subsequent token.",
        default=0.0,
        ge=0,
        json_schema_extra=UiSchema(widget=Widgets.float),
    )


class AnthropicReasoningParameters(AnthropicBaseParameters):
    thinking: bool = Field(
        title="Enable Thinking",
        default=False,
        description="Enable the model to 'think' through problems step-by-step.",
        json_schema_extra=UiSchema(widget=Widgets.toggle),
    )
    budget_tokens: int = Field(
        title="Thinking Token Budget",
        description="Determines how many tokens Claude can use for its internal reasoning process.",
        default=1024,
        ge=1024,
    )

    @field_validator("budget_tokens", mode="before")
    def ensure_value_is_less_than_model_max(cls, value: int, info):
        return _validate_token_limit(value, info)

    @field_validator("thinking", mode="before")
    def check_temperature(value: bool, info):
        if value and info.context.get("temperature", 0) != 1:
            raise PydanticCustomError(
                "invalid_model_parameters",
                "Thinking can only be used with a temperature of 1",
            )
        return value


def get_schema(model):
    """Get resolved schema for a model. This is so the frontend can render it properly."""
    schema = resolve_references(model.model_json_schema())
    schema.pop("$defs", None)
    return schema


def get_all_subclasses(cls):
    """Recursively retrieves all subclasses of a given class"""
    all_subclasses = set()
    for subclass in cls.__subclasses__():
        all_subclasses.add(subclass)
        all_subclasses.update(get_all_subclasses(subclass))
    return all_subclasses


param_classes = get_all_subclasses(LLMModelParamBase)
LLM_MODEL_PARAMETER_SCHEMAS = {cls.__name__: get_schema(cls) for cls in param_classes}
