import dataclasses
from abc import abstractmethod
from typing import Annotated, Any, ClassVar, Generic, Protocol, TypeVar, _AnnotatedAlias

from django import forms
from pydantic import BaseModel

from ..service_providers.llm_service import LlmService
from .exceptions import StepError
from .log import Logger

PipeIn = TypeVar("PipeIn", contravariant=True)
PipeOut = TypeVar("PipeOut", covariant=True)


@dataclasses.dataclass
class StepContext(Generic[PipeOut]):
    """Context for a step in a pipeline. This is used as input and output for each step."""

    data: PipeOut
    name: str = "start"
    persist: bool = True
    is_multiple: bool = False
    metadata: dict = dataclasses.field(default_factory=dict)

    @classmethod
    def initial(cls, data: PipeOut = None):
        return cls(data)

    def clone_with(self, data: PipeOut = None):
        if data is None:
            data = self.data
        return dataclasses.replace(self, data=data)


class PipelineContext:
    """Context for a pipeline. This is passed to each step before it is run."""

    def __init__(self, llm_service: LlmService, logger: Logger = None, params: dict = None):
        self.llm_service = llm_service
        self.log = logger or Logger()
        self.params = params or {}
        self.llm_provider = None


class Step(Protocol[PipeIn, PipeOut]):
    """Step protocol. This is the interface for a step in a pipeline."""

    input_type: ClassVar
    output_type: ClassVar

    def initialize(self, pipeline_context: PipelineContext):
        ...

    @abstractmethod
    def __call__(self, context: StepContext[PipeIn]) -> StepContext[PipeOut]:
        ...


class Pipeline:
    """A pipeline is a sequence of steps that are run in order. A pipeline is valid when
    the steps match the input and output types."""

    def __init__(self, steps: list[Step], name: str = None, description: str = None):
        self.name = name
        self.description = description
        self.steps = steps
        self.context_chain = []
        self._validate_input_output_types()

    def __str__(self):
        ret = f"{self.name or 'Pipeline'}"
        if self.description:
            ret += f": {self.description}"
        return ret

    def _validate_input_output_types(self):
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


PARAM_REQUIRED = "param_required"


def required(origin):
    """Mark a parameter as required.

    This can be used for annotating parameter fields in a Params class:

        class MyParams(Params):
            my_field: required(int) = None

    MyParams().check() will raise an error if my_field is None.
    """
    if isinstance(origin, _AnnotatedAlias):
        origin.__metadata__ = origin.__metadata__ + (PARAM_REQUIRED,)
        return origin
    return Annotated[origin, PARAM_REQUIRED]


class ParamsForm(forms.Form):
    """Base class for parameter forms. This is used to edit the parameters for a step."""

    form_name = None

    def __init__(self, request, *args, **kwargs):
        self.request = request
        initial = kwargs.get("initial")
        if initial:
            kwargs["initial"] = self.reformat_initial(initial)
        super().__init__(*args, **kwargs)

    def reformat_initial(self, initial):
        """Override this to change the structure of the initial data which comes from serialized parameter objects."""
        return initial

    def clean(self):
        self.get_params()
        return self.cleaned_data

    def save(self) -> "Params":
        return self.get_params()

    def get_params(self):
        """Return a Params object from the form data."""
        raise NotImplementedError


class Params(BaseModel):
    """Base class for step parameters. This is a pydantic model that can be used to
    validate and serialize parameters for a step. The class must be able to be instantiated
    with no arguments. Use the `required` type annotation to mark fields as required.

    Subclasses can override get_form_class() to provide a form for editing the parameters.
    """

    def get_form_class(self) -> type[ParamsForm] | None:
        return None

    def merge(self, *params: dict) -> "Params":
        """Merge data into the current params, overriding any existing values"""
        original = self.model_dump(exclude_unset=True)
        updated = {}
        for data in params:
            updated |= data
        updated |= original
        dump = self.model_validate(updated).model_dump(exclude_defaults=True)
        return self.__class__(**dump)

    def check(self):
        """Check that required fields are set."""
        for name, field in self.model_fields.items():
            for metadata in field.metadata:
                if metadata == PARAM_REQUIRED and getattr(self, name) is None:
                    raise ValueError(f"Missing required parameter {name}")


class NoParams(Params):
    pass


class BaseStep(Generic[PipeIn, PipeOut]):
    """Base class for steps in a pipeline. This is a callable that takes a StepContext
    and returns a new StepContext.

    Subclasses must implement the run() method.
    """

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
        try:
            with self.log(self.name):
                self._params.check()
                self.preflight_check(context)

                self.log.debug(f"Params: {self._params}")
                result = self.run(self._params, context.data)
                result.name = self.name
                return result
        finally:
            self.log.info(f"Step {self.name} complete")

    def run(self, params: Params, data: PipeIn) -> StepContext[PipeOut]:
        """Run the step and return the output data and metadata."""
        raise NotImplementedError

    def preflight_check(self, context: StepContext):
        """Perform any preflight checks on the input data or pipeline context."""
        pass
