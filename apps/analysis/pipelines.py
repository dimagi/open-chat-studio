from apps.analysis.steps.loaders import CommCareAppLoader, ResourceTextLoader
from apps.analysis.steps.parsers import WhatsappParser

from .core import ParamsForm, Pipeline
from .steps.filters import TimeseriesFilter
from .steps.processors import AssistantStep, JinjaTemplateStep, LlmCompletionStep
from .steps.splitters import TimeseriesSplitter

TEXT_DATA_PIPE = "text_data"
COMMCARE_APP_PIPE = "commcare_app"
FILTERED_WHATSAPP_DATA_PIPE = "filtered_whatsapp_data"
SPLIT_WHATSAPP_DATA_PIPE = "split_whatsapp_data"

LLM_PIPE = "llm"
ASSISTANT_PIPE = "assistant"


class PiplineDefinition:
    def __init__(self, steps: list[type], name: str, description: str):
        self.name = name
        self.description = description
        self.steps = steps

    def build(self, id_prefix: str):
        steps = [step(step_id=f"{id_prefix}:{i}") for i, step in enumerate(self.steps)]
        return Pipeline(steps)

    def __str__(self):
        return f"{self.name}: {self.description}"


SOURCE_PIPELINES = {
    TEXT_DATA_PIPE: PiplineDefinition(
        [
            ResourceTextLoader,
        ],
        "Text Data",
        "Load text data from a file",
    ),
    COMMCARE_APP_PIPE: PiplineDefinition(
        [
            CommCareAppLoader,
            JinjaTemplateStep,
        ],
        "CommCare Application",
        "Load data from a CommCare application API and format it with a Jinja template.",
    ),
    FILTERED_WHATSAPP_DATA_PIPE: PiplineDefinition(
        [
            ResourceTextLoader,
            WhatsappParser,
            TimeseriesFilter,
        ],
        "Filtered WhatsApp Data",
        "Load WhatsApp data from a file and filter it by date.",
    ),
    SPLIT_WHATSAPP_DATA_PIPE: PiplineDefinition(
        [
            ResourceTextLoader,
            WhatsappParser,
            TimeseriesSplitter,
        ],
        "Split WhatsApp Data",
        "Load whatsApp data from a file and split it into chunks by date.",
    ),
}


PIPELINES = {
    LLM_PIPE: PiplineDefinition(
        [
            LlmCompletionStep,
        ],
        "LLM Processing",
        "Pass data to the LLM with a prompt",
    ),
    ASSISTANT_PIPE: PiplineDefinition(
        [
            AssistantStep,
        ],
        "OpenAI Assistant",
        "Pass data to an OpenAI Assistant with a prompt",
    ),
}

# TODO: use a class based registry


def get_source_pipeline_options() -> list[tuple[str, str]]:
    return [(name, str(pipeline_def)) for name, pipeline_def in SOURCE_PIPELINES.items()]


def get_source_pipeline(name: str) -> Pipeline:
    return SOURCE_PIPELINES[name].build("source")


def get_data_pipeline_options() -> list[tuple[str, str]]:
    return [(name, str(pipeline_def)) for name, pipeline_def in PIPELINES.items()]


def get_data_pipeline(name: str) -> Pipeline:
    return PIPELINES[name].build("data")


def get_static_param_forms(pipeline) -> dict[str, type[ParamsForm]]:
    forms_by_step = {step.name: step.params.get_static_config_form_class() for step in pipeline.steps}
    return dict((step_id, form_class) for step_id, form_class in forms_by_step.items() if form_class)


def get_dynamic_param_forms(pipeline) -> dict[str, type[ParamsForm]]:
    forms_by_step = {step.name: step.params.get_dynamic_config_form_class() for step in pipeline.steps}
    return dict((step_id, form_class) for step_id, form_class in forms_by_step.items() if form_class)


def get_static_forms_for_analysis(analysis):
    source_pipeline = get_source_pipeline(analysis.source)
    return {
        **get_static_param_forms(source_pipeline),
        **get_static_param_forms(get_data_pipeline(analysis.pipeline)),
    }


def get_dynamic_forms_for_analysis(analysis):
    source_pipeline = get_source_pipeline(analysis.source)
    return {
        **get_dynamic_param_forms(source_pipeline),
        **get_dynamic_param_forms(get_data_pipeline(analysis.pipeline)),
    }
