import dataclasses
from abc import abstractmethod
from typing import Annotated, Any, ClassVar, Generic, Protocol, TypeVar

from django import forms
from pydantic import BaseModel

from ..service_providers.llm_service import LlmService
from .log import LogEntry, Logger

PipeIn = TypeVar("PipeIn", contravariant=True)
PipeOut = TypeVar("PipeOut", covariant=True)


@dataclasses.dataclass
class StepContext(Generic[PipeOut]):
    data: PipeOut
    name: str = "start"
    logs: list[LogEntry] = dataclasses.field(default_factory=list)
    metadata: dict = dataclasses.field(default_factory=dict)

    @classmethod
    def initial(cls, data: PipeOut = None):
        return cls(data, "start", [], {})

    @property
    def log_str(self):
        return "\n".join([str(log) for log in self.logs])


class PipelineContext:
    def __init__(self, llm_service: LlmService, logger: Logger = None, params: dict = None):
        self.llm_service = llm_service
        self.log = logger or Logger()
        self.params = params or {}
        self.llm_provider = None


class Step(Protocol[PipeIn, PipeOut]):
    output_multiple: bool
    input_type: ClassVar
    output_type: ClassVar

    def initialize(self, pipeline_context: PipelineContext):
        ...

    @abstractmethod
    def __call__(self, context: StepContext[PipeIn]) -> StepContext[PipeOut]:
        ...


class Pipeline:
    def __init__(self, steps: list[Step], name: str = None, description: str = None):
        self.name = name
        self.description = description
        self.steps = steps
        self.context_chain = []
        self._validate()

    def __str__(self):
        ret = f"{self.name or 'Pipeline'}"
        if self.description:
            ret += f": {self.description}"
        return ret

    def _validate(self):
        steps = iter(list(self.steps))
        step = next(steps)
        while steps and step.output_type == Any:
            step = next(steps)
        current_out_type = step.output_type
        for step in steps:
            if step.input_type != Any and step.input_type != current_out_type:
                raise StepError(f"Type mismatch: {step}, {step.input_type} != {current_out_type}")
            if step.output_type != Any:
                current_out_type = step.output_type

    def run(self, pipeline_context: PipelineContext, initial_context: StepContext) -> StepContext:
        self.context_chain.append(initial_context)
        for step in self.steps:
            step.initialize(pipeline_context)
            out_context = step(self.context_chain[-1])
            self.context_chain.append(out_context)
        return self.context_chain[-1]


P = TypeVar("P", bound="Params")
PARAM_REQUIRED = "param_required"


def required(type_: type):
    """Mark a parameter as required"""
    return Annotated[type_, PARAM_REQUIRED]


class ParamsForm(forms.Form):
    form_name = None

    def __init__(self, request, *args, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)

    def save(self) -> "Params":
        raise NotImplementedError


class Params(BaseModel):
    def get_form_class(self) -> type[ParamsForm] | None:
        return None

    def merge(self, *params: dict) -> P:
        """Merge data into the current params, overriding any existing values"""
        original = self.model_dump(exclude_unset=True)
        updated = {}
        for data in params:
            updated |= data
        updated |= original
        dump = self.model_validate(updated).model_dump(exclude_defaults=True)
        return self.__class__(**dump)

    def check(self):
        for name, field in self.model_fields.items():
            for metadata in field.metadata:
                if metadata == PARAM_REQUIRED and getattr(self, name) is None:
                    raise ValueError(f"Missing required parameter {name}")


class NoParams(Params):
    pass


class BaseStep(Generic[PipeIn, PipeOut]):
    output_multiple = False
    input_type: PipeIn
    output_type: PipeOut
    param_schema: type[Params] = NoParams

    def __init__(self, params: Params = None):
        self._params = params or self.param_schema()
        self.pipeline_context = None

    @property
    def log(self):
        return self.pipeline_context.log

    @property
    def name(self):
        return self.__class__.__name__

    def initialize(self, pipeline_context: PipelineContext):
        self.pipeline_context = pipeline_context
        self._params = self._params.merge(self.pipeline_context.params, self.pipeline_context.params.get(self.name, {}))

    def __call__(self, context: StepContext[PipeIn]) -> StepContext[PipeOut]:
        self.log.info(f"Running step {self.name}")
        with self.log(self.name):
            self._params.check()
            self.check_context(context)

            self.log.debug(f"Params: {self._params}")
            output, metadata = self.run(self._params, context.data)
            return StepContext(output, self.name, self.log.log_entries(), metadata)

    def run(self, params: Params, data: PipeIn) -> tuple[PipeOut, dict]:
        raise NotImplementedError

    def check_context(self, context: StepContext):
        pass


class StepError(Exception):
    pass
