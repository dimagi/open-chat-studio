from enum import Enum

from pydantic import BaseModel, Field

from apps.custom_actions.schema_utils import resolve_references
from apps.pipelines.nodes.base import UiSchema, Widgets


class OpenAIReasoningEffort(Enum):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class OpenAIVerbosityLevel(Enum):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class OpenAIReasoningSummary(Enum):
    AUTO = "auto", "Auto"
    DETAILED = "detailed", "Detailed"


class OpenAIReasoningParameters(BaseModel):
    effort: str = Field(
        title="Reasoning Effort",
        default="low",
        json_schema_extra=UiSchema(
            widget=Widgets.select, enum_labels=[item.value[0] for item in OpenAIReasoningEffort]
        ),
    )

    summary: str = Field(
        title="Reasoning Summary",
        default="auto",
        json_schema_extra=UiSchema(
            widget=Widgets.select, enum_labels=[item.value[0] for item in OpenAIReasoningSummary]
        ),
    )


class OpenAIReasoningWithVerbosityParameters(OpenAIReasoningParameters):
    verbosity: str = Field(
        title="medium",
        json_schema_extra=UiSchema(widget=Widgets.select, enum_labels=[item.value[0] for item in OpenAIVerbosityLevel]),
    )


def get_schema(model):
    schema = resolve_references(model.model_json_schema())
    schema.pop("$defs", None)
    return schema


def parameter_schemas() -> dict[str, dict]:
    return {
        "OpenAIReasoningParameters": get_schema(OpenAIReasoningParameters),
        "OpenAIReasoningWithVerbosityParameters": get_schema(OpenAIReasoningWithVerbosityParameters),
    }


ParameterTypes = OpenAIReasoningParameters | OpenAIReasoningWithVerbosityParameters
