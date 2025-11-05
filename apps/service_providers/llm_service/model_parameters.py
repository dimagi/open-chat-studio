from django.db.models import TextChoices
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from apps.custom_actions.schema_utils import resolve_references
from apps.pipelines.nodes.base import UiSchema, Widgets


class OpenAIReasoningEffortParameter(TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class OpenAIReasoningSummaryParameter(TextChoices):
    AUTO = "auto", "Auto"
    DETAILED = "detailed", "Detailed"


class LLMModelParamBase(BaseModel):
    pass


class OpenAINonReasoningParameters(LLMModelParamBase):
    max_output_tokens: int = Field(
        title="Max Output Tokens",
        default=128000,
        description="The maximum number of tokens to generate in the completion.",
        ge=1,
    )

    top_p: float = Field(
        title="Top P",
        default=0.0,
        ge=0.0,
        le=1.0,
        json_schema_extra=UiSchema(widget=Widgets.float),
    )

    @field_validator("max_output_tokens", mode="before")
    def ensure_value_is_less_than_model_max(cls, value: int, info):
        value = int(value)
        model_max_token_limit = info.context.get("model_max_token_limit", None)
        if value >= model_max_token_limit:
            raise PydanticCustomError(
                "invalid_model_parameters",
                "This value must be less than the model's max token limit",
                {"field": "max_output_tokens"},
            )
        return value


class OpenAIReasoningParameters(LLMModelParamBase):
    effort: OpenAIReasoningEffortParameter = Field(
        title="Reasoning Effort",
        default="low",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIReasoningEffortParameter.labels),
    )


def get_schema(model):
    """Get resolved schema for a model. This is so the frontend can render it properly."""
    schema = resolve_references(model.model_json_schema())
    schema.pop("$defs", None)
    return schema


param_classes = LLMModelParamBase.__subclasses__()
LLM_MODEL_PARAMETER_SCHEMAS = {cls.__name__: get_schema(cls) for cls in param_classes}
