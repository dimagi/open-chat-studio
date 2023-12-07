from apps.analysis.steps.loaders import ResourceTextLoader
from apps.analysis.steps.parsers import WhatsappParser

from .core import ParamsForm, Pipeline
from .steps.filters import TimeseriesFilter
from .steps.processors import AssistantStep, LlmCompletionStep
from .steps.splitters import TimeseriesSplitter

LLM_PIPE = "llm"

TEXT_DATA_PIPE = "text_data"

SOURCE_PIPELINES = {
    TEXT_DATA_PIPE: Pipeline(
        [
            ResourceTextLoader(),
        ],
        "Text Data",
        "Load text data from a file",
    ),
    "filtered_whatsapp_data": Pipeline(
        [
            ResourceTextLoader(),
            WhatsappParser(),
            TimeseriesFilter(),
        ],
        "Filtered WhatsApp Data",
        "Load WhatsApp data from a file and filter it by date.",
    ),
    "split_whatsapp_data": Pipeline(
        [
            ResourceTextLoader(),
            WhatsappParser(),
            TimeseriesSplitter(),
        ],
        "Split WhatsApp Data",
        "Load whatsApp data from a file and split it into chunks by date.",
    ),
}


PIPELINES = {
    LLM_PIPE: Pipeline(
        [
            LlmCompletionStep(),
        ],
        "LLM Processing",
        "Pass data to the LLM with a prompt",
    ),
    "assistant": Pipeline(
        [
            AssistantStep(),
        ],
        "OpenAI Assistant",
        "Pass data to an OpenAI Assistant with a prompt",
    ),
}

# TODO: use a class based registry


def get_source_pipeline_options() -> list[tuple[str, str]]:
    return [(name, str(pipeline)) for name, pipeline in SOURCE_PIPELINES.items()]


def get_source_pipeline(name: str) -> Pipeline:
    return SOURCE_PIPELINES[name]


def get_data_pipeline_options() -> list[tuple[str, str]]:
    return [(name, str(pipeline)) for name, pipeline in PIPELINES.items()]


def get_data_pipeline(name: str) -> Pipeline:
    return PIPELINES[name]


def get_param_forms(pipeline) -> dict[str, type[ParamsForm]]:
    forms_by_step = {step.name: step.param_schema().get_form_class() for step in pipeline.steps}
    return dict((name, form_class) for name, form_class in forms_by_step.items() if form_class)
