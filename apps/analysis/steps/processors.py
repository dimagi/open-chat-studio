import time
from functools import cached_property
from typing import Annotated, Any

import pydantic
from langchain.chat_models.base import BaseChatModel
from langchain.prompts import PromptTemplate
from openai._types import NOT_GIVEN
from openai.types import FileObject
from pydantic import model_validator

import apps.analysis.exceptions
from apps.analysis import core
from apps.analysis.core import ParamsForm, StepContext, required


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
                raise ValueError(f"Invalid prompt template. Prompts must have a 'data' variable.")
            elif len(input_variables) > 1:
                raise ValueError(f"Invalid prompt template. Prompts must only have a 'data' variable.")
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

    def run(self, params: LlmCompletionStepParams, data: Any) -> StepContext[str]:
        llm: BaseChatModel = self.pipeline_context.llm_service.get_chat_model(params.llm_model, 1.0)
        prompt = params.prompt_template.format_prompt(data=data)
        result = llm.invoke(prompt)
        return StepContext(result.content, name="llm_output")


class AssistantParams(PromptParams):
    assistant_id: required(str) = None
    # TODO: passing files to the assistant
    # file_ids: list[str] = None

    def get_static_config_form_class(self) -> type[ParamsForm] | None:
        from .forms import AssistantParamsForm

        return AssistantParamsForm


class AssistantOutput(pydantic.BaseModel):
    response: str = None
    files: Annotated[list[FileObject], pydantic.Field(default_factory=list)]

    def add_file(self, file):
        self.files.append(file)


class AssistantStep(core.BaseStep[Any, str]):
    """Experimental assistant step."""

    param_schema = AssistantParams
    input_type = Any
    output_type = str
    client = None

    def preflight_check(self, context: core.StepContext):
        llm_service = self.pipeline_context.llm_service
        if not llm_service.supports_assistant:
            raise apps.analysis.exceptions.StepError(f"'{llm_service.type}' LLM does not support assistants")

        self.client = self.pipeline_context.llm_service.get_raw_client()

    def run(self, params: AssistantParams, data: Any) -> StepContext[str]:
        result = AssistantOutput()
        prompt = params.prompt_template.format_prompt(data=data)
        thread = self.client.beta.threads.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt.text,
                    # "file_ids": params.file_ids,
                }
            ]
        )
        self.log.info(f"Assistant Thread created")

        run = self.client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=params.assistant_id,
        )
        self.log.info("Running query with assistant")

        run = self._wait_for_run(run.id, thread.id)
        if run.status != "completed":
            self.log.error(f"Run failed with status {run.status}")
            raise apps.analysis.exceptions.StepError(f"Assistant run failed with status {run.status}")

        result.response = self._process_messages(result, thread.id)
        # TODO: Record files
        return StepContext(result.response, metadata={"thread_id": thread.id, "run_id": run.id})

    def _process_messages(self, result: AssistantOutput, thread_id: str) -> str:
        messages = list(self.client.beta.threads.messages.list(thread_id=thread_id, order="asc"))
        self.log.debug(f"Analysis completed. Got {len(messages)} messages")

        for message in messages:
            message_content = message.content[0].text
            annotations = message_content.annotations
            citations = []

            # Iterate over the annotations and add footnotes
            for index, annotation in enumerate(annotations):
                # Replace the text with a footnote
                message_content.value = message_content.value.replace(annotation.text, f"[{index}]")

                # Gather citations based on annotation attributes
                if file_citation := getattr(annotation, "file_citation", None):
                    cited_file = self.client.files.retrieve(file_citation.file_id)
                    result.add_file(cited_file)
                    citations.append(f"[{index}] {file_citation.quote} from {cited_file.filename}")
                    self.log.info(f"Received file {cited_file.filename} from assistant")
                elif file_path := getattr(annotation, "file_path", None):
                    cited_file = self.client.files.retrieve(file_path.file_id)
                    result.add_file(cited_file)
                    citations.append(f"[{index}] Click <here> to download {cited_file.filename}")
                    self.log.info(f"Received file {cited_file.filename} from assistant")

            # Add footnotes to the end of the message before displaying to user
            message_content.value += "\n" + "\n".join(citations)
        return "\n".join([message.content[0].text.value for message in messages])

    def _wait_for_run(self, run_id: str, thread_id: str) -> Any:
        in_progress = True
        last_step = NOT_GIVEN
        while in_progress:
            run = self.client.beta.threads.runs.retrieve(run_id, thread_id=thread_id)
            in_progress = run.status in ("in_progress", "queued")
            if in_progress:
                time.sleep(2)

            steps = list(self.client.beta.threads.runs.steps.list(thread_id=thread_id, run_id=run_id, after=last_step))
            for step in steps:
                if step.status != "in_progress":
                    details = step.step_details
                    if details.type == "message_creation":
                        details = f"Created message {details.message_creation.message_id}"
                    elif details.type == "tool_calls":
                        details = f"Ran tool {details.tool_calls}"
                    self.log.debug(f"Step: {step.id} ({step.status}): {details}")
                    last_step = step.id
        return run
