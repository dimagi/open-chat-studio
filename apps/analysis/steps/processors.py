import mimetypes
import pathlib
import time
from functools import cached_property
from io import BytesIO
from typing import Annotated, Any

import openai
import pydantic
from langchain.chat_models.base import BaseChatModel
from langchain.prompts import PromptTemplate
from openai.types import FileObject
from openai.types.beta.threads import MessageContentImageFile, MessageContentText
from pydantic import model_validator

import apps.analysis.exceptions
from apps.analysis import core
from apps.analysis.core import ParamsForm, StepContext, required
from apps.analysis.exceptions import StepError
from apps.analysis.models import Resource, ResourceMetadata, ResourceType
from apps.analysis.serializers import temporary_data_file


class PromptParams(core.Params):
    """Base class for steps that use a prompt.

    The prompt template must have a variable named 'data' that will be
    filled in with the data from the pipeline."""

    prompt: required(str) = None

    @cached_property
    def prompt_template(self):
        if self.prompt:
            return PromptTemplate.from_template(self.prompt)

    @model_validator(mode="after")
    def check_prompt_inputs(self) -> "LlmCompletionStepParams":
        if self.prompt_template:
            input_variables = set(self.prompt_template.input_variables)
            if "data" not in input_variables:
                raise ValueError("Invalid prompt template. Prompts must have a 'data' variable.")
            elif len(input_variables) > 1:
                raise ValueError("Invalid prompt template. Prompts must only have a 'data' variable.")
        return self


class LlmCompletionStepParams(PromptParams):
    llm_model: required(str) = None

    def get_static_config_form_class(self):
        from apps.analysis.steps.forms import LlmCompletionStepParamsForm

        return LlmCompletionStepParamsForm

    def get_dynamic_config_form_class(self):
        from apps.analysis.steps.forms import LlmCompletionStepParamsForm

        return LlmCompletionStepParamsForm


class LlmCompletionStep(core.BaseStep[Any, str]):
    """Pass the incoming data to the LLM in the prompt and return the result."""

    param_schema = LlmCompletionStepParams
    input_type = Any
    output_type = str

    def run(self, params: LlmCompletionStepParams, context: StepContext[Any]) -> StepContext[str]:
        llm: BaseChatModel = self.pipeline_context.llm_service.get_chat_model(params.llm_model, 1.0)
        prompt = params.prompt_template.format_prompt(data=context.get_data())
        try:
            result = llm.invoke(prompt)
        except Exception as e:
            raise StepError(f"Error invoking LLM: {e}")
        return StepContext(result.content, name="llm_output")


class AssistantParams(core.Params):
    assistant_id: required(str) = None
    prompt: required(str) = None
    # file_ids: list[str] = None

    def get_static_config_form_class(self) -> type[ParamsForm] | None:
        from .forms import StaticAssistantParamsForm

        return StaticAssistantParamsForm

    def get_dynamic_config_form_class(self) -> type[ParamsForm] | None:
        from .forms import DynamicAssistantParamsForm

        return DynamicAssistantParamsForm


class AssistantOutput(pydantic.BaseModel):
    response: str = ""
    files: Annotated[list[FileObject], pydantic.Field(default_factory=list)]

    def add_file(self, file, content_type=None):
        self.files.append(file)


class AssistantStep(core.BaseStep[Any, str]):
    """Experimental assistant step."""

    param_schema = AssistantParams
    input_type = Any
    output_type = str
    client = None

    def preflight_check(self, context: core.StepContext):
        llm_service = self.pipeline_context.llm_service
        if not llm_service.supports_assistants:
            raise apps.analysis.exceptions.StepError(f"'{llm_service.type}' LLM does not support assistants")

        self.client = self.pipeline_context.llm_service.get_raw_client()

    def run(self, params: AssistantParams, context: StepContext[Any]) -> StepContext[str]:
        try:
            openai_file = self.create_file(context)
        except openai.APIStatusError as e:
            raise StepError("Unable to create file for assistant.", e)

        thread = self.client.beta.threads.create(
            messages=[{"role": "user", "content": params.prompt, "file_ids": [openai_file.id]}]
        )
        self.log.info(f"Assistant Thread created ({thread.id})")

        if self.is_cancelled:
            return StepContext("")

        run = self.client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=params.assistant_id,
        )
        self.log.info(f"Running query with assistant ({run.id})")

        run = self._wait_for_run(run.id, thread.id)
        if run.status in ("cancelling", "cancelled"):
            return StepContext("", metadata={"thread_id": thread.id, "run_id": run.id})

        if run.status != "completed":
            self.log.error(f"Run failed with status {run.status}")
            raise apps.analysis.exceptions.StepError(f"Assistant run failed with status {run.status}")

        result = self._process_messages(thread.id)
        return StepContext(result.response, metadata={"thread_id": thread.id, "run_id": run.id})

    def create_file(self, context):
        openai_file = None
        if context.resource:
            file_id = context.resource.wrapped_metadata.openai_file_id
            if file_id:
                self.log.info(f"Using existing resource {context.resource.id}")
                openai_file = self.client.files.retrieve(file_id)
            else:
                self.log.info(f"Uploading resource {context.resource.id} to assistant")
                with context.resource.file.open("rb") as fh:
                    bytesio = BytesIO(fh.read())
                openai_file = self.client.files.create(file=bytesio, purpose="assistants")
                context.resource.metadata["openai_file_id"] = openai_file.id
                context.resource.save()
        if not openai_file:
            self.log.info("Uploading data to assistant")
            with temporary_data_file(context.get_data()) as file:
                openai_file = self.client.files.create(file=file, purpose="assistants")
        return openai_file

    def _process_messages(self, thread_id: str) -> AssistantOutput:
        output = AssistantOutput()
        messages = list(self.client.beta.threads.messages.list(thread_id=thread_id, order="asc"))
        # ignore user message
        self.log.debug(f"Analysis completed. Got {len(messages)} messages")

        for message in messages:
            if message.role == "user":
                continue
            for content in message.content:
                if isinstance(content, MessageContentImageFile):
                    resource = self.make_resource_from_file(content.image_file.file_id, ResourceType.IMAGE)
                    output.response += get_resource_markdown_link(resource, image=True)
                elif isinstance(content, MessageContentText):
                    message_content = content.text
                    annotations = message_content.annotations
                    citations = []

                    # Iterate over the annotations and add footnotes
                    for index, annotation in enumerate(annotations):
                        # Replace the text with a footnote
                        message_content.value = message_content.value.replace(f"({annotation.text})", f"[{index}]")

                        # Gather citations based on annotation attributes
                        if file_citation := getattr(annotation, "file_citation", None):
                            cited_file = self.client.files.retrieve(file_citation.file_id)
                            citations.append(f"[{index}]: {file_citation.quote} from {cited_file.filename}")
                        elif file_path := getattr(annotation, "file_path", None):
                            resource = self.make_resource_from_file(file_path.file_id, ResourceType.UNKNOWN)
                            citations.append(f"[{index}]: resource:{resource.team.slug}:{resource.id}")

                    # Add footnotes to the end of the message before displaying to user
                    output.response += "\n" + message_content.value + "\n" + "\n".join(citations)
        return output

    def _wait_for_run(self, run_id: str, thread_id: str) -> Any:
        in_progress = True
        seen = set()
        while in_progress:
            if self.is_cancelled:
                try:
                    run = self.client.beta.threads.runs.cancel(thread_id=thread_id, run_id=run_id)
                except openai.BadRequestError:
                    pass
                in_progress = False
            else:
                run = self.client.beta.threads.runs.retrieve(run_id, thread_id=thread_id)
                in_progress = run.status in ("in_progress", "queued")
                if in_progress:
                    time.sleep(2)

            steps = list(self.client.beta.threads.runs.steps.list(thread_id=thread_id, run_id=run_id))
            for step in steps:
                if step.id in seen:
                    continue
                if step.status != "in_progress":
                    details = step.step_details
                    if details.type == "message_creation":
                        log_message_creation(self, thread_id, details.message_creation)
                    elif details.type == "tool_calls":
                        for call in details.tool_calls:
                            log_tool_call(self, call)
                    self.log.debug(f"Step: {step.status} ({step.id})")
                    seen.add(step.id)
        return run

    def make_resource_from_file(self, file_id, resource_type):
        file = self.client.files.retrieve(file_id)
        filename = file.filename
        try:
            filename = pathlib.Path(file.filename).name
        except Exception:
            pass

        content_type = mimetypes.guess_type(filename)[0]
        self.log.info(f"Received {resource_type} file {filename} from assistant")
        content = self.client.files.content(file.id)
        metadata = ResourceMetadata(
            type="", format=resource_type, data_schema={}, openai_file_id=file.id, content_type=content_type
        )
        return self.create_resource(content.read(), filename, force=True, serialize=False, metadata=metadata)


def get_resource_markdown_link(resource: Resource, link_text: str = None, image=False) -> str:
    link_text = link_text or resource.name
    prefix = "!" if image else ""
    return f"{prefix}[{link_text}](resource:{resource.team.slug}:{resource.id})\n"


def log_message_creation(step, thread_id, message_creation):
    message = step.client.beta.threads.messages.retrieve(thread_id=thread_id, message_id=message_creation.message_id)
    content = "\n".join([content.text.value for content in message.content if isinstance(content, MessageContentText)])
    step.log.info(f"{message.role}: {content}")


def log_tool_call(step, call):
    step.log.info(f"Calling tool: {call.type}")
    match call.type:
        case "code_interpreter":
            step.log.debug(f"\n{call.code_interpreter.input}")
            for output in call.code_interpreter.outputs:
                match output.type:
                    case "logs":
                        step.log.debug(f"\nLogs:\n{output.logs}")
                    case "image":
                        step.log.debug(f"\nImage:\n{output.image.file_id}")
        case "retrieval":
            pass
        case "function":
            step.log.debug(f"\nCall:\n{call.function.name} ({call.function.arguments})")
            step.log.debug(f"\nOutput:\n{call.function.output}")
