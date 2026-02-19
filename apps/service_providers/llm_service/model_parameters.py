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


class GPT51ReasoningEffortParameter(TextChoices):
    NONE = "none", "None"
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class GPT52ReasoningEffortParameter(TextChoices):
    NONE = "none", "None"
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    XHIGH = "xhigh", "XHigh"


class ClaudeOpus46EffortParameter(TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    MAX = "max", "Max"


class OpenAIVerbosityParameter(TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class LLMModelParamBase(BaseModel):
    pass


class BasicParameters(LLMModelParamBase):
    """Parameters common to non-reasoning models"""

    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, title="Temperature", json_schema_extra=UiSchema(widget=Widgets.range)
    )


class OpenAIReasoningParameters(LLMModelParamBase):
    effort: OpenAIReasoningEffortParameter = Field(
        title="Reasoning Effort",
        default=OpenAIReasoningEffortParameter.MEDIUM,
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIReasoningEffortParameter.labels),
    )


class GPT5Parameters(LLMModelParamBase):
    effort: GPT5ReasoningEffortParameter = Field(
        title="Reasoning Effort",
        default=GPT5ReasoningEffortParameter.MEDIUM,
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=GPT5ReasoningEffortParameter.labels),
    )

    verbosity: OpenAIVerbosityParameter = Field(
        title="Verbosity",
        default=OpenAIVerbosityParameter.MEDIUM,
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIVerbosityParameter.labels),
    )


class GPT51Parameters(LLMModelParamBase):
    effort: GPT51ReasoningEffortParameter = Field(
        title="Reasoning Effort",
        default=GPT51ReasoningEffortParameter.NONE,
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=GPT51ReasoningEffortParameter.labels),
    )

    verbosity: OpenAIVerbosityParameter = Field(
        title="Verbosity",
        default=OpenAIVerbosityParameter.MEDIUM,
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIVerbosityParameter.labels),
    )


class GPT52Parameters(LLMModelParamBase):
    effort: GPT52ReasoningEffortParameter = Field(
        title="Reasoning Effort",
        default=GPT52ReasoningEffortParameter.MEDIUM,
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=GPT52ReasoningEffortParameter.labels),
    )

    verbosity: OpenAIVerbosityParameter = Field(
        title="Verbosity",
        default=OpenAIVerbosityParameter.MEDIUM,
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIVerbosityParameter.labels),
    )

    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        title="Temperature",
        description="Only supported when reasoning effort is set to 'none'",
        json_schema_extra=UiSchema(widget=Widgets.range),
    )

    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        title="Top P",
        description="Only supported when reasoning effort is set to 'none'",
        json_schema_extra=UiSchema(widget=Widgets.range),
    )

    @field_validator("temperature", mode="before")
    def validate_temperature(cls, value: float, info):
        if value is not None and info.data.get("effort") != "none":
            raise PydanticCustomError(
                "invalid_model_parameters",
                "Temperature can only be set when reasoning effort is 'none'",
            )
        elif value is None and info.data.get("effort") == "none":
            raise PydanticCustomError(
                "invalid_model_parameters",
                "Temperature must be set when reasoning effort is 'none'",
            )
        return value

    @field_validator("top_p", mode="before")
    def validate_top_p(cls, value: float, info):
        if value is not None and info.data.get("effort") != "none":
            raise PydanticCustomError(
                "invalid_model_parameters",
                "Top P can only be set when reasoning effort is 'none'",
            )
        elif value is None and info.data.get("effort") == "none":
            raise PydanticCustomError(
                "invalid_model_parameters",
                "Top P must be set when reasoning effort is 'none'",
            )
        return value


class GPT5ProParameters(LLMModelParamBase):
    # gpt-5-pro only supports high effort, which is also its default
    verbosity: OpenAIVerbosityParameter = Field(
        title="Verbosity",
        default=OpenAIVerbosityParameter.MEDIUM,
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIVerbosityParameter.labels),
    )


class AnthropicBaseParameters(BasicParameters):
    max_tokens: int = Field(
        title="Max Output Tokens",
        default=32000,
        description="The maximum number of tokens to generate in the completion.",
        ge=1,
        le=64000,
    )


class ClaudeHaikuLatestParameters(AnthropicBaseParameters):
    max_tokens: int = Field(
        title="Max Output Tokens",
        default=8192,
        description="The maximum number of tokens to generate in the completion.",
        ge=1,
        le=8192,
    )


class ClaudeOpus4_20250514Parameters(AnthropicBaseParameters):
    max_tokens: int = Field(
        title="Max Output Tokens",
        default=32000,
        description="The maximum number of tokens to generate in the completion.",
        ge=1,
        le=32000,
    )


class AnthropicNonReasoningParameters(AnthropicBaseParameters):
    top_k: int = Field(
        title="Top K",
        description="Only sample from the top K options for each subsequent token.",
        default=0,
        ge=0,
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
    def ensure_value_is_less_than_max_output_tokens(cls, value: int, info):
        if not info.data.get("thinking", False):
            # No need to validate if thinking is not enabled
            return value

        value = int(value or 0)
        if value >= info.data.get("max_tokens", 0):
            raise PydanticCustomError(
                "invalid_model_parameters",
                f"Tokens must be less than the model's max output token limit of {info.data.get('max_tokens')}",
            )
        return value

    @field_validator("thinking", mode="before")
    def check_temperature(cls, value: bool, info):
        # Only when thinking is disabled can the model's temperature be adjusted
        if value and info.data.get("temperature") != 1.0:
            raise PydanticCustomError(
                "invalid_model_parameters",
                "Thinking can only be used with a temperature of 1.0",
            )
        return value


class ClaudeOpus46Parameters(BasicParameters):
    temperature: float | None = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        title="Temperature",
        description="Only supported when reasoning effort is set to 'none'",
        json_schema_extra=UiSchema(widget=Widgets.range),
    )
    max_tokens: int = Field(
        title="Max Output Tokens",
        default=32000,
        description="The maximum number of tokens to generate in the completion.",
        ge=1,
        le=128000,
    )
    effort: ClaudeOpus46EffortParameter = Field(
        title="Reasoning Effort",
        default=ClaudeOpus46EffortParameter.HIGH,
        description="Control intelligence, speed, and cost tradeoffs with adaptive thinking.",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=ClaudeOpus46EffortParameter.labels),
    )
    adaptive_thinking: bool = Field(
        title="Enable Adaptive Thinking",
        default=False,
        description="Let Claude dynamically decide when and how much to think with adaptive thinking mode. "
        "At the default effort level (high), Claude will almost always think. "
        "At lower effort levels, Claude may skip thinking for simpler problems.",
        json_schema_extra=UiSchema(widget=Widgets.toggle),
    )

    @field_validator("adaptive_thinking", mode="before")
    def check_temperature(cls, value: bool, info):
        # Only when thinking is disabled can the model's temperature be adjusted
        if value and info.data.get("temperature") != 1.0:
            raise PydanticCustomError(
                "invalid_model_parameters",
                "Thinking can only be used with a temperature of 1.0",
            )
        return value


class ClaudeSonnet46Parameters(BasicParameters):
    temperature: float | None = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        title="Temperature",
        description="Only supported when reasoning effort is set to 'none'",
        json_schema_extra=UiSchema(widget=Widgets.range),
    )
    max_tokens: int = Field(
        title="Max Output Tokens",
        default=32000,
        description="The maximum number of tokens to generate in the completion.",
        ge=1,
        le=64000,
    )
    effort: ClaudeOpus46EffortParameter = Field(
        title="Reasoning Effort",
        default=ClaudeOpus46EffortParameter.HIGH,
        description="Control intelligence, speed, and cost tradeoffs with adaptive thinking.",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=ClaudeOpus46EffortParameter.labels),
    )
    adaptive_thinking: bool = Field(
        title="Enable Adaptive Thinking",
        default=False,
        description="Let Claude dynamically decide when and how much to think with adaptive thinking mode. "
        "At the default effort level (high), Claude will almost always think. "
        "At lower effort levels, Claude may skip thinking for simpler problems.",
        json_schema_extra=UiSchema(widget=Widgets.toggle),
    )

    @field_validator("adaptive_thinking", mode="before")
    def check_temperature(cls, value: bool, info):
        # Only when thinking is disabled can the model's temperature be adjusted
        if value and info.data.get("temperature") != 1.0:
            raise PydanticCustomError(
                "invalid_model_parameters",
                "Thinking can only be used with a temperature of 1.0",
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
