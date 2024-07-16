from abc import ABCMeta, abstractmethod
from functools import cache

from django.utils import timezone
from langchain_core.callbacks import BaseCallbackHandler

from apps.annotations.models import Tag, TagCategories
from apps.channels.models import ChannelPlatform
from apps.chat.agent.tools import get_tools
from apps.chat.conversation import compress_chat_history
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import Experiment, ExperimentSession
from apps.utils.time import pretty_date


class RunnableState(metaclass=ABCMeta):
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
    def format_input(self, input_key, runnable_input):
        pass

    @abstractmethod
    def get_participant_data(self):
        pass

    @abstractmethod
    def get_participant_timezone(self):
        pass

    @abstractmethod
    def get_current_datetime(self):
        pass

    @abstractmethod
    def get_prompt(self):
        pass


class ExperimentState(RunnableState):
    def __init__(self, experiment: Experiment, session: ExperimentSession):
        self.experiment = experiment
        self.session = session

    @cache
    def get_llm_service(self):
        return self.experiment.get_llm_service()

    def get_chat_model(self):
        return self.get_llm_service().get_chat_model(self.experiment.llm, self.experiment.temperature)

    @property
    def callback_handler(self):
        return self.get_llm_service().get_callback_handler(self.get_chat_model())

    def format_input(self, input_key: str, runnable_input: dict):
        if self.experiment.input_formatter:
            runnable_input[input_key] = self.experiment.input_formatter.format(input=runnable_input[input_key])
        return runnable_input

    @property
    def is_unauthorized_participant(self):
        """Returns `true` if a participant is unauthorized. A participant is considered authorized when the
        following conditions are met:
        For web channels:
        - They are a platform user
        All other channels:
        - Always True, since the external channel handles authorization
        """
        return self.session.experiment_channel.platform == ChannelPlatform.WEB and self.session.participant.user is None

    def get_participant_data(self):
        if self.is_unauthorized_participant:
            return ""
        return self.session.get_participant_data(use_participant_tz=True) or ""

    def get_participant_timezone(self):
        return self.session.get_participant_timezone()

    def get_current_datetime(self):
        return pretty_date(timezone.now(), self.get_participant_timezone())

    def get_prompt(self):
        return self.experiment.prompt_text

    def get_tools(self):
        return get_tools(self.session)


class ChatRunnableState(RunnableState):
    @abstractmethod
    def get_chat_history(self, input_messages: list):
        pass

    def get_source_material(self):
        pass

    @abstractmethod
    def save_message_to_history(self, message: str, type_: ChatMessageType, experiment_tag: str = None):
        pass

    @abstractmethod
    def check_cancellation(self):
        pass

    @abstractmethod
    def get_tools(self):
        pass


class ChatExperimentState(ExperimentState, ChatRunnableState):
    def get_chat_history(self, input_messages: list):
        return compress_chat_history(
            self.session.chat,
            llm=self.get_chat_model(),
            max_token_limit=self.experiment.max_token_limit,
            input_messages=input_messages,
        )

    def get_source_material(self):
        return self.experiment.source_material.material if self.experiment.source_material else ""

    def save_message_to_history(self, message: str, type_: ChatMessageType, experiment_tag: str = None):
        chat_message = ChatMessage.objects.create(
            chat=self.session.chat,
            message_type=type_.value,
            content=message,
        )
        if experiment_tag:
            tag, _ = Tag.objects.get_or_create(
                name=experiment_tag,
                team=self.session.team,
                is_system_tag=True,
                category=TagCategories.BOT_RESPONSE,
            )
            chat_message.add_tag(tag, team=self.session.team, added_by=None)

    def check_cancellation(self):
        self.session.chat.refresh_from_db(fields=["metadata"])
        # temporary mechanism to cancel the chat
        # TODO: change this to something specific to the current chat message
        return self.session.chat.metadata.get("cancelled", False)


class AssistantState(RunnableState):
    @abstractmethod
    def get_assistant_instructions(self) -> str:
        pass

    @abstractmethod
    def get_openai_assistant(self):
        pass

    @abstractmethod
    def get_metadata(self, key: Chat.MetadataKeys):
        pass

    @abstractmethod
    def set_metadata(self, key: Chat.MetadataKeys, value):
        pass

    @abstractmethod
    def save_message_to_history(self, message: str, type_: ChatMessageType):
        pass


class AssistantExperimentState(ExperimentState, AssistantState):
    def get_assistant_instructions(self):
        # Langchain doesn't support the `additional_instructions` parameter that the API specifies, so we have to
        # override the instructions if we want to pass in dynamic data.
        # https://github.com/langchain-ai/langchain/blob/cccc8fbe2fe59bde0846875f67aa046aeb1105a3/libs/langchain/langchain/agents/openai_assistant/base.py#L491
        return self.experiment.assistant.instructions.format(
            participant_data=self.get_participant_data(), current_datetime=self.get_current_datetime()
        )

    def get_openai_assistant(self):
        return self.experiment.assistant.get_assistant()

    @property
    def chat(self):
        return self.session.chat

    def get_metadata(self, key: Chat.MetadataKeys):
        """Chat metadata"""
        return self.chat.get_metadata(key)

    def set_metadata(self, key: Chat.MetadataKeys, value):
        self.chat.set_metadata(key, value)

    def save_message_to_history(self, message: str, type_: ChatMessageType):
        return ChatMessage.objects.create(
            chat=self.session.chat,
            message_type=type_.value,
            content=message,
        )
