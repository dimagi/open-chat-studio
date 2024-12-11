"""
This module provides adapter classes to facilitate interaction with an LLM service or OpenAI Assistant within the
context of an experiment or pipeline.

Classes:
    - ChatAdapter: An adapter for handling chat interactions using an LLM service.
    - AssistantAdapter: An adapter for handling interactions with an OpenAI Assistant.

Usage:
    Use the `for_experiment` or `for_pipeline` class methods to instantiate `ChatAdapter` or `AssistantAdapter`.
"""
from abc import ABCMeta
from functools import cached_property
from typing import TYPE_CHECKING, Self

from langchain_core.prompts import PromptTemplate, get_template_variables

from apps.assistants.models import OpenAiAssistant
from apps.chat.agent.tools import get_assistant_tools, get_tool_instances, get_tools
from apps.chat.models import Chat, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.service_providers.llm_service.main import LlmService, OpenAIAssistantRunnable
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext

if TYPE_CHECKING:
    from apps.pipelines.nodes.nodes import AssistantNode
    from apps.service_providers.models import LlmProviderModel


class BaseAdapter(metaclass=ABCMeta):
    ai_message = None

    @property
    def callback_handler(self):
        return self.get_llm_service().get_callback_handler(self.provider_model_name)

    def get_trace_metadata(self) -> dict:
        if self.trace_service:
            trace_info = self.trace_service.get_current_trace_info()
            if trace_info:
                return {
                    "trace_info": {**trace_info.model_dump(), "trace_provider": self.trace_service.type},
                }
        return {}

    def get_llm_service(self):
        return self.llm_service

    def format_input(self, input: str) -> str:
        if not self.input_formatter:
            return input

        template = PromptTemplate.from_template(self.input_formatter)
        context = self.template_context.get_context(template.input_variables)
        context["input"] = input
        return template.format(**context)

    def get_tools(self):
        return self.tools


class ChatAdapter(BaseAdapter):
    def __init__(
        self,
        session: ExperimentSession,
        provider_model_name: str,
        llm_service,
        temperature: float,
        prompt_text: str,
        max_token_limit: int,
        tools: list = None,
        input_formatter: str | None = None,
        source_material_id: int | None = None,
        trace_service=None,
        save_message_metadata_only=False,
    ):
        self.session = session
        self.provider_model_name = provider_model_name
        self.llm_service = llm_service
        self.temperature = temperature
        self.prompt_text = prompt_text
        self.max_token_limit = max_token_limit
        self.tools = tools or []
        self.input_formatter = input_formatter
        self.source_material_id = source_material_id
        self.trace_service = trace_service

        self.team = session.team
        self.template_context = PromptTemplateContext(session, source_material_id)
        self.save_message_metadata_only = save_message_metadata_only

    @classmethod
    def for_experiment(cls, experiment: Experiment, session: ExperimentSession, trace_service=None) -> Self:
        return cls(
            session=session,
            provider_model_name=experiment.get_llm_provider_model_name(),
            llm_service=experiment.get_llm_service(),
            temperature=experiment.temperature,
            prompt_text=experiment.prompt_text,
            max_token_limit=experiment.max_token_limit,
            tools=get_tools(session, experiment=experiment),
            input_formatter=experiment.input_formatter,
            source_material_id=experiment.source_material_id,
            trace_service=trace_service,
        )

    @classmethod
    def for_pipeline(
        cls,
        session: ExperimentSession,
        node: "AssistantNode",
        llm_service: LlmService,
        provider_model: "LlmProviderModel",
    ) -> Self:
        return cls(
            session=session,
            provider_model_name=provider_model.name,
            llm_service=llm_service,
            temperature=node.llm_temperature,
            prompt_text=node.prompt,
            max_token_limit=provider_model.max_token_limit,
            tools=get_tool_instances(node.tools, session),
            input_formatter="{input}",
            source_material_id=node.source_material_id,
            trace_service=session.experiment.trace_service,  # TODO
            save_message_metadata_only=True,
        )

    def get_chat_model(self):
        return self.get_llm_service().get_chat_model(self.provider_model_name, self.temperature)

    def get_template_context(self, variables: list[str]):
        return self.template_context.get_context(variables)

    def get_prompt(self):
        return self.prompt_text

    def check_cancellation(self):
        self.session.chat.refresh_from_db(fields=["metadata"])
        # temporary mechanism to cancel the chat
        # TODO: change this to something specific to the current chat message
        return self.session.chat.metadata.get("cancelled", False)


class AssistantAdapter(BaseAdapter):
    def __init__(
        self,
        session: ExperimentSession,
        assistant: OpenAiAssistant,
        citations_enabled: bool,
        input_formatter: str | None = None,
        trace_service=None,
        save_message_metadata_only: bool = False,
    ):
        self.session = session
        self.assistant = assistant
        self.llm_service = assistant.get_llm_service()
        self.citations_enabled = citations_enabled
        self.input_formatter = input_formatter
        self.trace_service = trace_service
        self.save_message_metadata_only = save_message_metadata_only

        self.provider_model_name = assistant.llm_provider_model.name
        self.input_message_metadata = {}
        self.output_message_metadata = {}
        self.team = session.team

        self.tools = get_assistant_tools(assistant, experiment_session=session)
        self.template_context = PromptTemplateContext(session, source_material_id=None)

    @staticmethod
    def for_experiment(experiment: Experiment, session: ExperimentSession, trace_service=None) -> Self:
        return AssistantAdapter(
            session=session,
            assistant=experiment.assistant,
            citations_enabled=experiment.citations_enabled,
            input_formatter=experiment.input_formatter,
            trace_service=trace_service,
        )

    @staticmethod
    def for_pipeline(session: ExperimentSession, node: "AssistantNode") -> Self:
        assistant = OpenAiAssistant.objects.get(id=node.assistant_id)
        experiment = session.experiment
        return AssistantAdapter(
            session=session,
            assistant=assistant,
            citations_enabled=node.citations_enabled,
            input_formatter=node.input_formatter,
            trace_service=experiment.trace_service,
            save_message_metadata_only=True,
        )

    @cached_property
    def assistant_client(self):
        return self.assistant.get_assistant().client

    @property
    def assistant_tools_enabled(self):
        return self.assistant.tools_enabled

    @property
    def assistant_builtin_tools(self) -> list:
        return self.assistant.builtin_tools

    @property
    def thread_id(self):
        return self.session.chat.get_metadata(Chat.MetadataKeys.OPENAI_THREAD_ID)

    @thread_id.setter
    def thread_id(self, value):
        key = Chat.MetadataKeys.OPENAI_THREAD_ID
        if self.trace_service:
            self.trace_service.update_trace({key: value})
        self.session.chat.set_metadata(key, value)

    def update_thread_id(self, thread_id: str):
        self.thread_id = thread_id

    def get_assistant_instructions(self):
        # Langchain doesn't support the `additional_instructions` parameter that the API specifies, so we have to
        # override the instructions if we want to pass in dynamic data.
        # https://github.com/langchain-ai/langchain/blob/cccc8fbe2fe59bde0846875f67aa046aeb1105a3/libs/langchain/langchain/agents/openai_assistant/base.py#L491
        instructions = self.assistant.instructions

        input_variables = get_template_variables(instructions, "f-string")
        if input_variables:
            context = PromptTemplateContext(self.session, None).get_context(input_variables)
            instructions = instructions.format(**context)

        code_interpreter_attachments = self.get_attachments(["code_interpreter"])
        if self.assistant.include_file_info and code_interpreter_attachments:
            file_type_info = self.get_file_type_info(code_interpreter_attachments)
            instructions += self.get_file_type_info_text(file_type_info)
        return instructions

    def get_file_type_info_text(self, file_type_infos: list[dict[str, str]]) -> str:
        instructions = "\n\nFile type information:\n\n| File Path | Mime Type |\n"
        for file_info in file_type_infos:
            for file_name, mime_type in file_info.items():
                instructions += f"| /mnt/data/{file_name} | {mime_type} |\n"
        return instructions

    def get_file_type_info(self, attachments: list) -> list:
        if not self.assistant.include_file_info:
            return ""
        file_type_info = []
        for att in attachments:
            file_type_info.extend([{file.external_id: file.content_type} for file in att.files.all()])
        return file_type_info

    def get_attachments(self, attachment_type: list[str]):
        return self.session.chat.attachments.filter(tool_type__in=attachment_type)

    def get_input_message_metadata(self, resource_file_mapping: dict[str, list[str]]):
        metadata = {"openai_thread_checkpoint": True, **self.get_trace_metadata()}
        file_ids = set([file_id for file_ids in resource_file_mapping.values() for file_id in file_ids])
        metadata["openai_file_ids"] = list(file_ids)
        return metadata

    def get_output_message_metadata(self, annotation_file_ids: list):
        metadata = {"openai_thread_checkpoint": True, **self.get_trace_metadata()}
        metadata["openai_file_ids"] = annotation_file_ids
        return metadata

    def get_messages_to_sync_to_thread(self):
        to_sync = []
        for message in self.session.chat.message_iterator(with_summaries=False):
            if message.get_metadata("openai_thread_checkpoint"):
                break
            to_sync.append(message)
        return [
            {
                "content": message.content,
                "role": message.role,
            }
            for message in reversed(to_sync)
            if message.message_type != "system"
        ]

    def get_openai_assistant(self) -> OpenAIAssistantRunnable:
        return self.assistant.get_assistant()

    def get_message_metadata(self, message_type: ChatMessageType) -> dict:
        """
        Retrieve metadata for a given message type.
        """
        return self.input_message_metadata if message_type == ChatMessageType.HUMAN else self.output_message_metadata
