from django.db.models import TextChoices
from pydantic import BaseModel, Field

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
