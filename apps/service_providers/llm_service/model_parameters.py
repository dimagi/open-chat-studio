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


class OpenAIReasoningParameters(BaseModel):
    effort: OpenAIReasoningEffortParameter = Field(
        title="Reasoning Effort",
        default="low",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIReasoningEffortParameter.labels),
    )

    summary: OpenAIReasoningSummaryParameter = Field(
        title="Reasoning Summary",
        default="auto",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=OpenAIReasoningSummaryParameter.labels),
    )


def get_schema(model):
    """Get resolved schema for a model. This is so the frontend can render it properly."""
    schema = resolve_references(model.model_json_schema())
    schema.pop("$defs", None)
    return schema


param_classes = [OpenAIReasoningParameters]
LLM_MODEL_PARAMETER_SCHEMAS = {cls.__name__: get_schema(cls) for cls in param_classes}
