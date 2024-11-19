from abc import ABCMeta, abstractmethod
from functools import cache, cached_property

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.prompts import PromptTemplate, get_template_variables

from apps.annotations.models import Tag, TagCategories
from apps.assistants.models import OpenAiAssistant
from apps.chat.agent.tools import get_tools
from apps.chat.conversation import compress_chat_history
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.service_providers.llm_service.main import OpenAIAssistantRunnable
from apps.service_providers.llm_service.prompt_context import PromptTemplateContext
from apps.teams.models import Team


class BaseRunnableState(metaclass=ABCMeta):
    ai_message: ChatMessage | None = None

    @abstractmethod
    def get_llm_service(self):
        pass

    @abstractmethod
    def get_chat_model(self):
        pass

    @property
    @abstractmethod
    def callback_handler(self) -> BaseCallbackHandler:
        pass

    @abstractmethod
    def format_input(self, input: str):
        pass

    @abstractmethod
    def get_prompt(self):
        pass

    @abstractmethod
    def set_chat_metadata(self, key: Chat.MetadataKeys, value):
        pass

    @property
    @abstractmethod
    def chat(self):
        pass


class ExperimentState(BaseRunnableState):
    def __init__(self, experiment: Experiment, session: ExperimentSession, trace_service=None):
        self.experiment = experiment
        self.session = session
        self.trace_service = trace_service or self.experiment.trace_service
        self.template_context = PromptTemplateContext(session, experiment.source_material_id)

    @property
    def chat(self):
        return self.session.chat

    @cache
    def get_llm_service(self):
        return self.experiment.get_llm_service()

    def get_chat_model(self):
        return self.get_llm_service().get_chat_model(
            self.experiment.get_llm_provider_model_name(), self.experiment.temperature
        )

    @property
    def callback_handler(self):
        return self.get_llm_service().get_callback_handler(self.experiment.get_llm_provider_model_name())

    def format_input(self, input: str) -> str:
        if not self.experiment.input_formatter:
            return input

        template = PromptTemplate.from_template(self.experiment.input_formatter)
        context = self.template_context.get_context(template.input_variables)
        context["input"] = input
        return template.format(**context)

    def get_prompt(self):
        return self.experiment.prompt_text

    def get_tools(self):
        return get_tools(self.session, self.experiment)

    def get_trace_metadata(self) -> dict:
        if self.trace_service:
            trace_info = self.trace_service.get_current_trace_info()
            if trace_info:
                return {
                    "trace_info": {**trace_info.model_dump(), "trace_provider": self.trace_service.type},
                }
        return {}

    def set_chat_metadata(self, key: Chat.MetadataKeys, value):
        if self.trace_service:
            self.trace_service.update_trace({key: value})
        self.chat.set_metadata(key, value)


class ChatExperimentState(ExperimentState):
    def get_chat_history(self, input_messages: list):
        return compress_chat_history(
            self.chat,
            llm=self.get_chat_model(),
            max_token_limit=self.experiment.max_token_limit,
            input_messages=input_messages,
        )

    def save_message_to_history(self, message: str, type_: ChatMessageType, experiment_tag: str = None):
        chat_message = ChatMessage.objects.create(
            chat=self.chat, message_type=type_.value, content=message, metadata=self.get_trace_metadata()
        )
        if experiment_tag:
            tag, _ = Tag.objects.get_or_create(
                name=experiment_tag,
                team=self.session.team,
                is_system_tag=True,
                category=TagCategories.BOT_RESPONSE,
            )
            chat_message.add_tag(tag, team=self.session.team, added_by=None)

        if type_ == ChatMessageType.AI:
            self.ai_message = chat_message
            if not self.experiment.is_working_version:
                chat_message.add_system_tag(
                    tag=self.experiment.version_display, tag_category=TagCategories.EXPERIMENT_VERSION
                )

    def check_cancellation(self):
        self.chat.refresh_from_db(fields=["metadata"])
        # temporary mechanism to cancel the chat
        # TODO: change this to something specific to the current chat message
        return self.chat.metadata.get("cancelled", False)


class BaseAssistantState(BaseRunnableState):
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
            instructions += "\n\nFile type information:\n\n| File Path | Mime Type |\n"
            for file_info in file_type_info:
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

    def get_attachments(self, attachment_type: str):
        return self.chat.attachments.filter(tool_type__in=attachment_type)

    @cached_property
    def raw_client(self):
        return self.get_openai_assistant().client

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
        for message in self.chat.message_iterator(with_summaries=False):
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

    def get_metadata(self, key: Chat.MetadataKeys):
        """Chat metadata"""
        return self.chat.get_metadata(key)

    def get_openai_assistant(self) -> OpenAIAssistantRunnable:
        return self.assistant.get_assistant()

    @property
    def tools_enabled(self):
        return self.assistant.tools_enabled

    @property
    def builtin_tools(self):
        self.assistant.builtin_tools

    @cached_property
    def team(self) -> Team:
        return self.assistant.team

    @abstractmethod
    def pre_run_hook(self, input, config, message_metadata):
        pass

    @abstractmethod
    def post_run_hook(self, output, config, message_metadata):
        pass

    @abstractmethod
    def citations_enabled(self) -> bool:
        pass

    @property
    @abstractmethod
    def assistant(self) -> OpenAiAssistant:
        pass

    @property
    @abstractmethod
    def chat(self):
        pass

    @abstractmethod
    def get_trace_metadata(self) -> dict:
        pass


class ExperimentAssistantState(ExperimentState, BaseAssistantState):
    def pre_run_hook(self, input, config, message_metadata):
        if config.get("configurable", {}).get("save_input_to_history", True):
            self.save_message_to_history(input, type_=ChatMessageType.HUMAN, message_metadata=message_metadata)

    def post_run_hook(self, output, config, message_metadata):
        experiment_tag = config.get("configurable", {}).get("experiment_tag")
        self.save_message_to_history(
            output, type_=ChatMessageType.AI, message_metadata=message_metadata, experiment_tag=experiment_tag
        )

    def save_message_to_history(
        self,
        message: str,
        type_: ChatMessageType,
        message_metadata: dict | None = None,
        experiment_tag: str | None = None,
    ):
        """
        Create a chat message and appends the file ids from each resource to the `openai_file_ids` array in the
        chat message metadata.
        Example resource_file_mapping: {"resource1": ["file1", "file2"], "resource2": ["file3", "file4"]}
        """
        message_metadata = message_metadata or {}
        chat_message = ChatMessage.objects.create(
            chat=self.chat,
            message_type=type_.value,
            content=message,
            metadata=message_metadata,
        )

        if experiment_tag:
            tag, _ = Tag.objects.get_or_create(
                name=experiment_tag,
                team=self.session.team,
                is_system_tag=True,
                category=TagCategories.BOT_RESPONSE,
            )
            chat_message.add_tag(tag, team=self.session.team, added_by=None)

        if type_ == ChatMessageType.AI:
            self.ai_message = chat_message
            if not self.experiment.is_working_version:
                chat_message.add_system_tag(
                    tag=self.experiment.version_display, tag_category=TagCategories.EXPERIMENT_VERSION
                )

        return chat_message

    @property
    def citations_enabled(self):
        return self.experiment.citations_enabled

    @property
    def assistant(self):
        return self.experiment.assistant
