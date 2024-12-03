from abc import ABCMeta, abstractmethod
from typing import Self

from langchain_core.language_models.chat_models import BaseChatModel

from apps.annotations.models import Tag, TagCategories
from apps.chat.conversation import compress_chat_history, compress_pipeline_chat_history
from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.pipelines.models import PipelineChatHistory, PipelineChatHistoryTypes


class BaseHistoryManager(metaclass=ABCMeta):
    @abstractmethod
    def get_chat_history(self, input_messages: list):
        pass

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
        if self.trace_service:
            trace_info = self.trace_service.get_current_trace_info()
            if trace_info:
                return {
                    "trace_info": {**trace_info.model_dump(), "trace_provider": self.trace_service.type},
                }
        return {}


class ExperimentHistoryManager(BaseHistoryManager):
    def __init__(
        self,
        session: ExperimentSession,
        experiment: Experiment | None = None,
        max_token_limit: int | None = None,
        chat_model: BaseChatModel | None = None,
        trace_service=None,
    ):
        self.session = session
        self.max_token_limit = max_token_limit
        self.chat_model = chat_model
        self.trace_service = trace_service

        # TODO: Think about passing this in as context metadata rather
        self.experiment_version_number = experiment.version_number
        self.experiment_is_a_version = experiment.is_a_version

    @classmethod
    def for_llm_chat(
        cls,
        session: ExperimentSession,
        experiment: Experiment,
        max_token_limit: int,
        chat_model: BaseChatModel | None = None,
        trace_service=None,
    ) -> Self:
        return cls(
            session=session,
            experiment=experiment,
            max_token_limit=max_token_limit,
            chat_model=chat_model,
            trace_service=trace_service,
        )

    @classmethod
    def for_assistant(cls, session: ExperimentSession, experiment: Experiment) -> Self:
        return cls(session=session, experiment=experiment)

    def get_chat_history(self, input_messages: list):
        return compress_chat_history(
            self.session.chat,
            llm=self.chat_model,
            max_token_limit=self.max_token_limit,
            input_messages=input_messages,
        )

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

        if save_output_to_history:
            self.save_message_to_history(
                output,
                type_=ChatMessageType.AI,
                message_metadata=output_message_metadata,
                experiment_tag=experiment_tag,
            )


class PipelineHistoryManager(BaseHistoryManager):
    def __init__(
        self,
        session: ExperimentSession | None = None,
        node_id: str | None = None,
        history_type: PipelineChatHistoryTypes = PipelineChatHistoryTypes.GLOBAL,
        history_name: str | None = None,
        max_token_limit: int | None = None,
        chat_model: BaseChatModel | None = None,
    ):
        self.session = session
        self.node_id = node_id
        self.history_type = history_type
        self.history_name = history_name
        self.max_token_limit = max_token_limit
        self.chat_model = chat_model
        self.trace_service = session.experiment.trace_service if session else None

        self.input_message_metadata = None
        self.output_message_metadata = None

    @classmethod
    def for_llm_chat(
        cls,
        session: ExperimentSession,
        node_id: str,
        history_type: PipelineChatHistoryTypes,
        history_name: str,
        max_token_limit: int,
        chat_model: BaseChatModel,
    ) -> Self:
        return cls(
            session=session,
            node_id=node_id,
            history_type=history_type,
            history_name=history_name,
            max_token_limit=max_token_limit,
            chat_model=chat_model,
        )

    @classmethod
    def for_assistant(cls) -> Self:
        return cls()

    def get_chat_history(self, input_messages: list):
        if self.history_type == PipelineChatHistoryTypes.NONE or self.session is None:
            return []

        if self.history_type == PipelineChatHistoryTypes.GLOBAL:
            return compress_chat_history(
                chat=self.session.chat,
                llm=self.chat_model,
                max_token_limit=self.max_token_limit,
                input_messages=input_messages,
            )

        try:
            history: PipelineChatHistory = self.session.pipeline_chat_history.get(
                type=self.history_type, name=self._get_history_name(self.node_id)
            )
        except PipelineChatHistory.DoesNotExist:
            return []
        return compress_pipeline_chat_history(
            pipeline_chat_history=history,
            max_token_limit=self.max_token_limit,
            llm=self.chat_model,
            input_messages=input_messages,
        )

    def _get_history_name(self, node_id):
        if self.history_type == PipelineChatHistoryTypes.NAMED:
            return self.history_name
        return node_id

    def add_messages_to_history(
        self, input: str, input_message_metadata: dict, output: str, output_message_metadata: dict, *args, **kwargs
    ):
        self.input_message_metadata = input_message_metadata
        self.output_message_metadata = output_message_metadata

        if self.history_type == PipelineChatHistoryTypes.NONE:
            return

        if self.history_type == PipelineChatHistoryTypes.GLOBAL:
            # Global History is saved outside of the node
            return

        history, _ = self.session.pipeline_chat_history.get_or_create(
            type=self.history_type, name=self._get_history_name(self.node_id)
        )

        message = history.messages.create(human_message=input, ai_message=output, node_id=self.node_id)
        # TODO: Save normal session history here as well
        return message
