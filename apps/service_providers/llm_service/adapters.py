"""
This module provides adapter classes to facilitate interaction with an LLM service or OpenAI Assistant within the
context of an experiment or pipeline.

Classes:
    - ChatAdapter: An adapter for handling chat interactions using an LLM service.
    - AssistantAdapter: An adapter for handling interactions with an OpenAI Assistant.

Usage:
    Use the `for_experiment` or `for_pipeline` class methods to instantiate `ChatAdapter` or `AssistantAdapter`.
"""

from __future__ import annotations

import re
from functools import cached_property
from typing import TYPE_CHECKING, Self

from django.db import models
from langchain_core.prompts import PromptTemplate, get_template_variables
from langchain_core.tools import BaseTool

from apps.assistants.models import OpenAiAssistant, ToolResources
from apps.chat.agent.tools import OCS_CITATION_PATTERN, get_assistant_tools, get_tools
from apps.chat.models import Chat
from apps.experiments.models import Experiment, ExperimentSession
from apps.files.models import File
from apps.service_providers.llm_service.main import LlmService, OpenAIAssistantRunnable
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext

if TYPE_CHECKING:
    from apps.pipelines.nodes.base import PipelineState
    from apps.pipelines.nodes.nodes import AssistantNode, LLMResponseWithPrompt
    from apps.service_providers.models import LlmProviderModel


class BaseAdapter:
    ai_message = None

    @property
    def callback_handler(self):
        return self.get_llm_service().get_callback_handler(self.provider_model_name)

    def get_llm_service(self):
        return self.llm_service

    def format_input(self, input: str) -> str:
        if not self.input_formatter:
            return input

        template = PromptTemplate.from_template(self.input_formatter)
        context = self.template_context.get_context(template.input_variables)
        context["input"] = input
        return template.format(**context)

    def get_allowed_tools(self):
        if self.disabled_tools:
            # Model builtin tools doesn't have a name attribute and are dicts
            return [tool for tool in self.tools if hasattr(tool, "name") and tool.name not in self.disabled_tools]
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
        template_context: PromptTemplateContext,
        tools: list[BaseTool] = None,
        disabled_tools: set[str] = None,
        input_formatter: str | None = None,
        save_message_metadata_only=False,
    ):
        self.session = session
        self.provider_model_name = provider_model_name
        self.llm_service = llm_service
        self.temperature = temperature
        self.prompt_text = prompt_text
        self.max_token_limit = max_token_limit
        self.tools = tools or []
        self.disabled_tools = disabled_tools
        self.input_formatter = input_formatter

        self.team = session.team
        self.template_context = template_context
        self.save_message_metadata_only = save_message_metadata_only

    @classmethod
    def for_experiment(cls, experiment: Experiment, session: ExperimentSession) -> Self:
        return cls(
            session=session,
            provider_model_name=experiment.get_llm_provider_model_name(),
            llm_service=experiment.get_llm_service(),
            temperature=experiment.temperature,
            prompt_text=experiment.prompt_text,
            max_token_limit=experiment.max_token_limit,
            template_context=PromptTemplateContext(session, experiment.source_material_id, None),
            tools=get_tools(session, experiment=experiment),
            disabled_tools=None,  # not supported for simple experiments
            input_formatter=experiment.input_formatter,
        )

    @classmethod
    def for_pipeline(
        cls,
        session: ExperimentSession,
        node: LLMResponseWithPrompt,
        llm_service: LlmService,
        provider_model: LlmProviderModel,
        tools: list[BaseTool],
        pipeline_state: PipelineState,
        disabled_tools: set[str] = None,
    ) -> Self:
        extra_prompt_context = {
            "temp_state": pipeline_state.get("temp_state", {}),
            "session_state": session.state or {},
        }
        return cls(
            session=session,
            provider_model_name=provider_model.name,
            llm_service=llm_service,
            temperature=node.llm_temperature,
            prompt_text=node.prompt,
            max_token_limit=provider_model.max_token_limit,
            template_context=PromptTemplateContext(
                session,
                source_material_id=node.source_material_id,
                collection_id=node.collection_id,
                extra=extra_prompt_context,
            ),
            tools=tools,
            disabled_tools=disabled_tools,
            input_formatter="{input}",
            save_message_metadata_only=True,
        )

    def get_chat_model(self):
        return self.get_llm_service().get_chat_model(self.provider_model_name, self.temperature)

    def get_template_context(self, variables: list[str]) -> dict:
        return self.template_context.get_context(variables)

    def get_prompt(self):
        return self.prompt_text

    def check_cancellation(self):
        self.session.chat.refresh_from_db(fields=["metadata"])
        # temporary mechanism to cancel the chat
        # TODO: change this to something specific to the current chat message
        return self.session.chat.metadata.get("cancelled", False)

    def get_output_message_metadata(self, cited_files: list[File]) -> dict:
        """`cited_files` is a list of files that are cited in the response."""
        if not cited_files:
            return {}

        self.session.chat.attach_files(attachment_type="file_citation", files=cited_files)
        return {"cited_files": [file.id for file in cited_files]}

    def add_citation_section_from_cited_files(self, ai_message: str, cited_files: list[File]) -> str:
        """
        Parse the AI message for citations in the format <CIT the-file-id /> to build a reference section at the end.
        Each citation is replaced with a footnote-style reference [^1], [^2], etc., and a reference section is
        appended at the end of the message with download links for each cited file.

        Example:

        Input:
        ```
        Here is a fact <CIT 123 />. Here is another fact <CIT 456 />.
        ```

        Output:
        ```
        Here is a fact [^1]. Here is another fact [^2].

        [^1]: [file_123.txt](http://example.com/file_123.txt)
        [^2]: [file_456.pdf](http://example.com/file_456.pdf)
        ```
        """
        files = {file.id: file for file in cited_files}
        citation_pattern = re.compile(OCS_CITATION_PATTERN)
        tracked_file_ids = []
        file_references = {}  # Store references to avoid duplicates

        def replace_citations(match):
            """
            Replace a citation match with a footnote-style reference.
            The match is expected to be in the format <CIT 123 />
            """
            file_id = int(match.groupdict()["file_id"])
            if file_id not in files:
                # LLM hallucinated a file id. We don't want to show this to users, so remove the citation.
                return ""

            file = files[file_id]

            # Determine the citation index
            if file_id in tracked_file_ids:
                # If the file has already been cited, return the existing citation reference
                citation_index = tracked_file_ids.index(file_id) + 1
            else:
                citation_index = len(tracked_file_ids) + 1
                tracked_file_ids.append(file_id)

                # Store the reference for this file (only once)
                download_link = file.download_link(self.session.id)
                file_references[citation_index] = f"[^{citation_index}]: [{file.name}]({download_link})"

            return f"[^{citation_index}]"

        # Replace citations in the message
        ai_message = citation_pattern.sub(replace_citations, ai_message)

        # Build reference section from stored references
        if file_references:
            refs_section = "\n".join([file_references[i] for i in sorted(file_references.keys())])
            ai_message += "\n\n" + refs_section

        return ai_message


class AssistantAdapter(BaseAdapter):
    def __init__(
        self,
        session: ExperimentSession,
        assistant: OpenAiAssistant,
        citations_enabled: bool,
        input_formatter: str | None = None,
        save_message_metadata_only: bool = False,
        disabled_tools: set[str] = None,
    ):
        self.session = session
        self.assistant = assistant
        self.llm_service = assistant.get_llm_service()
        self.citations_enabled = citations_enabled
        self.input_formatter = input_formatter
        self.save_message_metadata_only = save_message_metadata_only

        self.provider_model_name = assistant.llm_provider_model.name
        self.team = session.team

        self.tools = get_assistant_tools(assistant, experiment_session=session)
        self.disabled_tools = disabled_tools
        self.template_context = PromptTemplateContext(session, source_material_id=None)

    @classmethod
    def for_experiment(cls, experiment: Experiment, session: ExperimentSession) -> Self:
        return cls(
            session=session,
            assistant=experiment.assistant,
            citations_enabled=experiment.citations_enabled,
            input_formatter=experiment.input_formatter,
            disabled_tools=None,  # not supported for simple experiments
        )

    @classmethod
    def for_pipeline(cls, session: ExperimentSession, node: AssistantNode, disabled_tools: set[str] = None) -> Self:
        assistant = OpenAiAssistant.objects.get(id=node.assistant_id)
        return cls(
            session=session,
            assistant=assistant,
            citations_enabled=node.citations_enabled,
            input_formatter=node.input_formatter,
            save_message_metadata_only=True,
            disabled_tools=disabled_tools,
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
            return []
        file_type_info = []
        for att in attachments:
            file_type_info.extend([{file.external_id: file.content_type} for file in att.files.all()])
        return file_type_info

    def get_attachments(self, attachment_type: list[str]):
        return self.session.chat.attachments.filter(tool_type__in=attachment_type)

    def get_input_message_metadata(self, resource_file_mapping: dict[str, list[str]]) -> dict:
        file_ids = set([file_id for file_ids in resource_file_mapping.values() for file_id in file_ids])
        return self._get_openai_metadata(list(file_ids))

    def get_output_message_metadata(self, annotation_file_ids: list) -> dict:
        return self._get_openai_metadata(annotation_file_ids)

    def _get_openai_metadata(self, annotation_file_ids: list):
        return {"openai_thread_checkpoint": True, "openai_file_ids": annotation_file_ids}

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

    def get_assistant_file_ids(self) -> list[str]:
        assistant_file_ids = ToolResources.objects.filter(assistant=self.assistant).values_list("files")
        return list(
            File.objects.filter(team_id=self.team.id, id__in=models.Subquery(assistant_file_ids)).values_list(
                "external_id", flat=True
            )
        )

    @property
    def allow_assistant_file_downloads(self):
        return self.assistant.allow_file_downloads
