from apps.analysis.steps.loaders import ResourceTextLoader
from apps.analysis.steps.parsers import WhatsappParser

from .core import ParamsForm, Pipeline
from .steps.processors import LlmCompletionStep

SOURCE_PIPELINES = {
    "text_data": Pipeline(
        [
            ResourceTextLoader(),
        ],
        "Text Data",
        "Load text data from a file",
    ),
    "whatsapp_data": Pipeline(
        [
            ResourceTextLoader(),
            WhatsappParser(),
        ],
        "WhatsApp Data",
        "Load WhatsApp data from a file",
    ),
}


PIPELINES = {
    "llm": Pipeline(
        [
            LlmCompletionStep(),
        ],
        "LLM Processing",
        "Pass data to the LLM with a prompt",
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
