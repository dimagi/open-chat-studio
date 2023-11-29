import typing

from .loaders import ResourceTextLoader
from .parsers import WhatsappParser
from .steps import Pipeline

if typing.TYPE_CHECKING:
    from .forms import ParamsForm


SOURCE_PIPELINES = {
    "whatsapp_data": Pipeline(
        [
            ResourceTextLoader(),
            WhatsappParser(),
        ],
        "WhatsApp Data",
        "Load WhatsApp data from a file",
    ),
}


def get_source_pipeline_options() -> list[tuple[str, str]]:
    return [(name, str(pipeline)) for name, pipeline in SOURCE_PIPELINES.items()]


def get_source_pipeline(name: str) -> Pipeline:
    return SOURCE_PIPELINES[name]


def get_param_forms(name: str) -> dict[str, type["ParamsForm"]]:
    pipeline = SOURCE_PIPELINES[name]
    forms_by_step = {step.name: step.param_schema().get_form_class() for step in pipeline.steps}
    return dict((name, form_class) for name, form_class in forms_by_step.items() if form_class)
