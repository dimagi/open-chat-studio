from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Self

from langchain_core.language_models.chat_models import BaseChatModel

from apps.annotations.models import Tag, TagCategories
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession


class BaseHistoryManager(metaclass=ABCMeta):
    @abstractmethod
    def add_messages_to_history(
        self,
        input: str,
        save_input_to_history: bool,
        input_message_metadata: dict,
        output: str,
        save_output_to_history: bool,
        experiment_tag: str,
        output_message_metadata: dict,
    ):
        pass


class ExperimentHistoryManager(BaseHistoryManager):
    def __init__(
        self,
        session: ExperimentSession,
        experiment: Experiment,
        trace_service,
        max_token_limit: int | None = None,
        chat_model: BaseChatModel | None = None,
        history_mode: str | None = None,
    ):
        self.session = session
        self.max_token_limit = max_token_limit
        self.chat_model = chat_model
        self.trace_service = trace_service
        self.ai_message = None
        self.history_mode = history_mode

        # TODO: Think about passing this in as context metadata rather
        self.experiment_version_number = experiment.version_number
        self.experiment_is_a_version = experiment.is_a_version

    @classmethod
    def for_assistant(cls, session: ExperimentSession, experiment: Experiment, trace_service) -> Self:
        return cls(session=session, experiment=experiment, trace_service=trace_service)

    def add_messages_to_history(
        self,
        input: str,
        save_input_to_history: bool,
        input_message_metadata: dict,
        output: str,
        save_output_to_history: bool,
        experiment_tag: str,
        output_message_metadata: dict,
    ):
        if save_input_to_history:
            self.save_message_to_history(input, type_=ChatMessageType.HUMAN, message_metadata=input_message_metadata)

        if output is not None and save_output_to_history:
            self.save_message_to_history(
                output,
                type_=ChatMessageType.AI,
                message_metadata=output_message_metadata,
                experiment_tag=experiment_tag,
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
        metadata = self.get_trace_metadata()
        metadata = metadata | (message_metadata or {})
        chat_message = ChatMessage.objects.create(
            chat=self.session.chat,
            message_type=type_.value,
            content=message,
            metadata=metadata,
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
            chat_message.add_version_tag(
                version_number=self.experiment_version_number, is_a_version=self.experiment_is_a_version
            )

        return chat_message

    def get_trace_metadata(self) -> dict:
        return self.trace_service.get_trace_metadata()


class AssistantPipelineHistoryManager(BaseHistoryManager):
    def __init__(self):
        self.input_message_metadata = {}
        self.output_message_metadata = {}

    def add_messages_to_history(  # ty: ignore[invalid-method-override]
        self, input: str, input_message_metadata: dict, output: str, output_message_metadata: dict, *args, **kwargs
    ):
        self.input_message_metadata = input_message_metadata
        self.output_message_metadata = self.output_message_metadata | output_message_metadata
