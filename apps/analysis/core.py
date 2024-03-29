import dataclasses
import logging
import uuid
from abc import abstractmethod
from collections.abc import Callable
from functools import cached_property
from typing import Annotated, Any, ClassVar, Generic, Protocol, TypeVar, _AnnotatedAlias

from django import forms
from pydantic import BaseModel
from sentry_sdk.integrations.logging import ignore_logger

from apps.service_providers.llm_service import LlmService
from apps.teams.models import Team

from .exceptions import StepError
from .models import AnalysisRun, Resource, ResourceMetadata
from .serializers import create_resource_for_data, create_resource_for_raw_data, get_serializer_by_name

PipeIn = TypeVar("PipeIn", contravariant=True)
PipeOut = TypeVar("PipeOut", covariant=True)


@dataclasses.dataclass
class StepContext(Generic[PipeOut]):
    """Context for a step in a pipeline. This is used as input and output for each step."""

    data: PipeOut
    name: str = ""
    metadata: dict = dataclasses.field(default_factory=dict)
    resource: Resource = None

    def get_or_create_resource(self, context: "PipelineContext"):
        if not self.resource:
            self.resource = context.create_resource(self.data, self.name)
        return self.resource

    @classmethod
    def initial(cls, data: PipeOut = None, resource=None, name: str = "Initial"):
        return cls(data=data, resource=resource, name=name)

    def get_data(self):
        if self.data is not None:
            return self.data
        if self.resource:
            metadata = self.resource.wrapped_metadata
            return get_serializer_by_name(metadata.type).read(self.resource.file, metadata)


@dataclasses.dataclass
class PipelineContext:
    """Context for a pipeline. This is passed to each step before it is run."""

    run: AnalysisRun = None
    log_handler_factory: Callable[[str], logging.Handler] = None
    params: dict = dataclasses.field(default_factory=dict)
    create_resources: bool = False

    _is_cancelled = False

    @cached_property
    def llm_service(self) -> LlmService:
        return self.run.group.analysis.llm_provider.get_llm_service()

    @cached_property
    def team(self) -> Team:
        return self.run.group.team

    @property
    def is_cancelled(self):
        if not self.run:
            return  # unit test
        if self._is_cancelled:
            return self._is_cancelled
        self.run.refresh_from_db(fields=["status"])
        self._is_cancelled = self.run.is_cancelled
        return self._is_cancelled

    def create_resource(
        self, data: Any, name: str, serialize: bool = True, metadata: ResourceMetadata = None
    ) -> Resource | None:
        if not self.create_resources:
            return
        qualified_name = f"{self.run.group.analysis.name}_{self.run.group.id}_{self.run.name}_{name}"
        if not serialize:
            assert metadata, "Metadata must be provided for raw data"
            resource = create_resource_for_raw_data(self.team, data, qualified_name, metadata)
        else:
            resource = create_resource_for_data(self.team, data, qualified_name)
        self.run.output_resources.add(resource)
        return resource


class Step(Protocol[PipeIn, PipeOut]):
    """Step protocol. This is the interface for a step in a pipeline."""

    input_type: ClassVar
    output_type: ClassVar

    @abstractmethod
    def invoke(self, context: StepContext[PipeIn], pipeline_context: PipelineContext) -> StepContext[PipeOut]:
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

    def run(self, pipeline_context: PipelineContext, initial_context: StepContext) -> StepContext | list[StepContext]:
        self.context_chain.append(initial_context)
        unique_id = uuid.uuid4().hex
        log = _create_logger("pipeline", unique_id)
        if pipeline_context.log_handler_factory:
            log.addHandler(pipeline_context.log_handler_factory(unique_id))

        def _run_step(step, context):
            if isinstance(context, list):
                log.info("Running step %s %s times", step.name, len(context))
                ret = []
                for i, ctx in enumerate(context):
                    ret.append(_run_step(step, ctx))
                log.info("Step %s complete", step.name)
                return ret
            else:
                return step.invoke(context, pipeline_context)

        for step in self.steps:
            out_context = _run_step(step, self.context_chain[-1])
            self.context_chain.append(out_context)
            if pipeline_context.is_cancelled:
                return self.context_chain[-1]
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

    def get_static_config_form_class(self) -> type[ParamsForm] | None:
        """Return a form class for editing the parameters. This is used for static configuration."""
        return None

    def get_dynamic_config_form_class(self) -> type[ParamsForm] | None:
        """Return a form class for editing the parameters. This is used for dynamic (runtime) configuration."""
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
    params: Params = NoParams()
    pipeline_context: PipelineContext | None = None
    name: str = None
    id: str = None

    def __init__(self, step_id=None, params: Params = None):
        self.params = params or self.params
        self.id = uuid.uuid4().hex
        self.log = _create_logger(self.name, self.id)
        self.name = self.__class__.__name__
        if step_id:
            self.name = f"{self.name}:{step_id}"

    @property
    def is_cancelled(self):
        return self.pipeline_context.is_cancelled

    def _initialize(self, pipeline_context: PipelineContext):
        self.pipeline_context = pipeline_context
        self.params = self.params.merge(self.pipeline_context.params, self.pipeline_context.params.get(self.name, {}))
        self.id = uuid.uuid4().hex
        self.log = _create_logger(self.name, self.id)
        if self.pipeline_context.log_handler_factory:
            self.log.addHandler(self.pipeline_context.log_handler_factory(self.id))

    def invoke(
        self, context: StepContext[PipeIn], pipeline_context: PipelineContext
    ) -> StepContext[PipeOut] | list[StepContext[PipeOut]]:
        self._initialize(pipeline_context)
        self.log.info(f"Running step {self.name}")
        try:
            self.params.check()
            self.preflight_check(context)

            self.log.debug(f"Params: {self.params}")
            result = self.run(self.params, context)
            for res in [result] if isinstance(result, StepContext) else result:
                if not res.name:
                    res.name = self.name
            return result
        finally:
            self.log.info(f"Step {self.name} complete")
            if self.pipeline_context.log_handler_factory:
                self.log.removeHandler(self.pipeline_context.log_handler_factory(self.id))

    def run(self, params: Params, context: StepContext[PipeIn]) -> StepContext[PipeOut] | list[StepContext[PipeOut]]:
        """Run the step and return the output data and metadata."""
        raise NotImplementedError

    def preflight_check(self, context: StepContext):
        """Perform any preflight checks on the input data or pipeline context."""
        pass

    def create_resource(
        self, data: Any, name: str, serialize=True, metadata: ResourceMetadata = None
    ) -> Resource | None:
        """Create a Resource for the data and add it to the step.
        This will only create resources if the pipeline context is configured to do so and this step is the last
        step in the pipeline (or force=True).
        """

        return self.pipeline_context.create_resource(data, name, serialize, metadata)


def _create_logger(name, unique_id):
    log_name = f"{name}_{unique_id}"
    log = logging.getLogger(log_name)
    log.propagate = False
    log.setLevel(logging.DEBUG)
    ignore_logger(log_name)
    return log
