from __future__ import annotations

import contextlib
import logging
import re
from abc import ABC, abstractmethod
from enum import Enum
from functools import cached_property
from io import BytesIO
from typing import TYPE_CHECKING, ClassVar

import emoji
import requests
from django.conf import settings
from django.db import transaction
from django.http import Http404
from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.util import antiflood, smart_split

from apps.annotations.models import TagCategories
from apps.channels import audio
from apps.channels.clients.connect_client import CommCareConnectClient
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.bots import EvalsBot, EventBot, get_bot
from apps.chat.exceptions import (
    AudioSynthesizeException,
    ChannelException,
    ParticipantNotAllowedException,
    VersionedExperimentSessionsNotAllowedException,
)
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.chat.tasks import STATUSES_FOR_COMPLETE_CHATS
from apps.events.models import StaticTriggerType
from apps.events.tasks import enqueue_static_triggers
from apps.experiments.models import (
    Experiment,
    ExperimentSession,
    Participant,
    ParticipantData,
    SessionStatus,
    VoiceResponseBehaviours,
)
from apps.files.models import File
from apps.service_providers.llm_service.history_managers import ExperimentHistoryManager
from apps.service_providers.llm_service.runnables import GenerationCancelled
from apps.service_providers.speech_service import SynthesizedAudio
from apps.service_providers.tracing import TraceInfo, TracingService
from apps.slack.utils import parse_session_external_id
from apps.teams.utils import current_team
from apps.users.models import CustomUser

if TYPE_CHECKING:
    from apps.channels.models import BaseMessage

logger = logging.getLogger("ocs.channels")

USER_CONSENT_TEXT = "1"
UNSUPPORTED_MESSAGE_BOT_PROMPT = """
Tell the user that they sent an unsupported message. You only support {supported_types} messages types.
"""
DEFAULT_ERROR_RESPONSE_TEXT = "Sorry, something went wrong while processing your message. Please try again later"

# The regex from https://stackoverflow.com/a/6041965 is used, but tweaked to remove capturing groups
URL_REGEX = r"(?:http|ftp|https):\/\/(?:[\w_-]+(?:(?:\.[\w_-]+)+))(?:[\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-])"

# Matches [^2]: [file_name](https://example.com)
MARKDOWN_REF_PATTERN = r"^\[(?P<ref>.+?)\]:\s*\[(?P<file_name>[^\]]+)\]\((?P<download_link>.*)\)"


def strip_urls_and_emojis(text: str) -> tuple[str, list[str]]:
    """Strips any URLs in `text` and appends them to the end of the text. Emoji's are filtered out"""
    text = emoji.replace_emoji(text, replace="")

    url_pattern = re.compile(URL_REGEX)
    urls = set(url_pattern.findall(text)) or []
    for url in urls:
        text = text.replace(url, "")

    return text, list(urls)


class MESSAGE_TYPES(Enum):
    TEXT = "text"
    VOICE = "voice"

    @staticmethod
    def is_member(value: str):
        return any(value == item.value for item in MESSAGE_TYPES)


class ChannelBase(ABC):
    """
    This class defines a set of common functions that all channels
    must implement. It provides a blueprint for tuning the behavior of the handler to suit specific channels.

    Args:
        experiment: An Experiment object representing the experiment associated with the handler.
        experiment_channel: An ExperimentChannel object representing the channel associated with the handler.
        experiment_session: An optional ExperimentSession object representing the experiment session associated
            with the handler.
    Raises:
        ChannelException: If both 'experiment_channel' and 'experiment_session' arguments are not provided.

    Class variables:
        voice_replies_supported: Indicates whether the channel supports voice messages
        supported_message_types: A list of message content types that are supported by this channel
        supports_conversational_consent_flow: Indicates whether the channel supports a conversational consent flow.

    Abstract methods:
        send_voice_to_user: (Optional) An abstract method to send a voice message to the user. This must be implemented
            if voice_replies_supported is True
        send_text_to_user: Implementation of sending text to the user. Typically this is the reply from the bot
        send_file_to_user: Implementation of sending a file to the user. This is a channel specific way of sending files
        _can_send_file: A method to check if a file can be sent through the channel.
        get_message_audio: The method to retrieve the audio content of the message from the external channel
        transcription_started:A callback indicating that the transcription process has started
        transcription_finished: A callback indicating that the transcription process has finished.
        submit_input_to_llm: A callback indicating that the user input will be given to the language model
    Public API:
        new_user_message: Handles a message coming from the user.
        send_message_to_user: Handles a message coming from the bot.
    """

    voice_replies_supported: ClassVar[bool] = False
    supported_message_types: ClassVar[str] = []
    supports_conversational_consent_flow: ClassVar[bool] = True

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        self.experiment = experiment
        self.experiment_channel = experiment_channel
        self._experiment_session = experiment_session
        self._message: BaseMessage = None
        self._participant_identifier = experiment_session.participant.identifier if experiment_session else None
        self._is_user_message = False
        self.trace_service = TracingService.create_for_experiment(self.experiment)

    @property
    def participant_id(self) -> int | None:
        if self.experiment_session:
            return self.experiment_session.participant.id
        return None

    @classmethod
    def start_new_session(
        cls,
        working_experiment: Experiment,
        experiment_channel: ExperimentChannel,
        participant_identifier: str,
        participant_user: CustomUser | None = None,
        session_status: SessionStatus = SessionStatus.ACTIVE,
        timezone: str | None = None,
        session_external_id: str | None = None,
        metadata: dict | None = None,
    ):
        return _start_experiment_session(
            working_experiment,
            experiment_channel,
            participant_identifier,
            participant_user,
            session_status,
            timezone,
            session_external_id,
            metadata,
        )

    @property
    def experiment_session(self) -> ExperimentSession:
        return self._experiment_session

    @experiment_session.setter
    def experiment_session(self, value: ExperimentSession):
        self._experiment_session = value
        self.reset_bot()

    @property
    def message(self) -> BaseMessage:
        return self._message

    @property
    def supports_multimedia(self) -> bool:
        return False

    @message.setter
    def message(self, value: BaseMessage):
        self._message = value
        self.reset_bot()
        self.reset_user_query()

    @cached_property
    def messaging_service(self):
        return self.experiment_channel.messaging_provider.get_messaging_service()

    @cached_property
    def bot(self):
        if not self.experiment_session:
            raise ChannelException("Bot cannot be accessed without an experiment session")
        return get_bot(self.experiment_session, self.experiment, self.trace_service)

    def reset_bot(self):
        with contextlib.suppress(AttributeError):
            del self.bot

    @property
    def participant_identifier(self) -> str:
        if self._participant_identifier is not None:
            return self._participant_identifier

        if self.experiment_session and self.experiment_session.participant.identifier:
            self._participant_identifier = self.experiment_session.participant.identifier
        elif self.message:
            self._participant_identifier = self.message.participant_id

        return self._participant_identifier

    @property
    def participant_user(self):
        if self.experiment_session:
            return self.experiment_session.participant.user

    @cached_property
    def participant_data(self) -> ParticipantData:
        experiment = self.experiment
        if self.experiment.is_a_version:
            experiment = self.experiment.working_version
        return experiment.participantdata_set.defer("data").get(participant__identifier=self.participant_identifier)

    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        raise NotImplementedError(
            "Voice replies are supported but the method reply (`send_voice_to_user`) is not implemented"
        )

    @abstractmethod
    def send_text_to_user(self, text: str):
        """Channel specific way of sending text back to the user"""
        raise NotImplementedError()

    def send_file_to_user(self, files: list[File]):  # noqa: B027
        """
        Sends the file to the user. This is a channel specific way of sending files.
        The default implementation does nothing.
        """
        pass

    def _can_send_file(self, file: File) -> bool:
        return False

    def get_message_audio(self) -> BytesIO:
        return self.messaging_service.get_message_audio(message=self.message)

    def echo_transcript(self, transcript: str):  # noqa: B027
        """Sends a text message to the user with a transcript of what the user said"""
        pass

    def transcription_started(self):  # noqa: B027
        """Callback indicating that the transcription process started"""
        pass

    def transcription_finished(self, transcript: str):  # noqa: B027
        """Callback indicating that the transcription is finished"""
        pass

    def submit_input_to_llm(self):  # noqa: B027
        """Callback indicating that the user input will now be given to the LLM"""
        pass

    def append_attachment_links(self, text: str, linkify_files: list[File]) -> str:
        """
        Appends the links of the files in `linkify_files` to the text.

        Example:
        ```
            This is a cat
        ```
        becomes

        ```
            This is a cat

            cat.jpg
            https://example.com/cat.jpg
        ```
        """
        if not linkify_files:
            return text

        links = [f"{file.name}\n{file.download_link(self.experiment_session.id)}\n" for file in linkify_files]
        return "{text}\n\n{links}".format(text=text, links="\n".join(links))

    @staticmethod
    def get_channel_class_for_platform(platform: ChannelPlatform | str) -> type[ChannelBase]:
        if platform == "telegram":
            channel_cls = TelegramChannel
        elif platform == "web":
            channel_cls = WebChannel
        elif platform == "whatsapp":
            channel_cls = WhatsappChannel
        elif platform == "facebook":
            channel_cls = FacebookMessengerChannel
        elif platform == "api":
            channel_cls = ApiChannel
        elif platform == "sureadhere":
            channel_cls = SureAdhereChannel
        elif platform == "slack":
            channel_cls = SlackChannel
        elif platform == "commcare_connect":
            channel_cls = CommCareConnectChannel
        # elif platform == "evaluations":
        #  evals channel can't be called this way
        else:
            raise Exception(f"Unsupported platform type {platform}")
        return channel_cls

    @staticmethod
    def from_experiment_session(experiment_session: ExperimentSession) -> ChannelBase:
        """Given an `experiment_session` instance, returns the correct ChannelBase subclass to use"""
        channel_cls = ChannelBase.get_channel_class_for_platform(experiment_session.experiment_channel.platform)
        return channel_cls(
            experiment_session.experiment,
            experiment_channel=experiment_session.experiment_channel,
            experiment_session=experiment_session,
        )

    @cached_property
    def user_query(self) -> str:
        """Returns the user query, extracted from whatever (supported) message type was used to convey the
        message
        """
        return self._extract_user_query()

    def reset_user_query(self):
        with contextlib.suppress(AttributeError):
            del self.user_query

    def _add_message(self, message: BaseMessage):
        """Adds the message to the handler in order to extract session information"""
        self.message = message

        if not self._participant_is_allowed():
            raise ParticipantNotAllowedException()

        self._ensure_sessions_exists()

    def new_user_message(self, message: BaseMessage) -> ChatMessage:
        """Handles the message coming from the user. Call this to send bot messages to the user.
        The `message` here will probably be some object, depending on the channel being used.
        """
        with current_team(self.experiment.team):
            self._is_user_message = True

            try:
                self._add_message(message)
            except ParticipantNotAllowedException:
                self.send_message_to_user("Sorry, you are not allowed to chat to this bot")
                return ChatMessage(content="Sorry, you are not allowed to chat to this bot")

            try:
                with self.trace_service.trace(
                    trace_name=self.experiment.name,
                    session=self.experiment_session,
                    inputs={"input": self.message.model_dump()},
                ) as span:
                    response = self._new_user_message()
                    span.set_outputs({"response": response.content})
                    return response
            except GenerationCancelled:
                return ChatMessage(content="", message_type=ChatMessageType.AI)

    def _participant_is_allowed(self):
        if self.experiment.is_public:
            return True
        return self.experiment.is_participant_allowed(self.participant_identifier)

    def _new_user_message(self) -> ChatMessage:
        try:
            if not self.is_message_type_supported():
                resp = self._handle_unsupported_message()
                return ChatMessage(content=resp)

            if self.supports_conversational_consent_flow:
                # Webchats' statuses are updated through an "external" flow
                if self._is_reset_conversation_request():
                    return ChatMessage(content="Conversation reset")

                if self.experiment.conversational_consent_enabled and self.experiment.consent_form_id:
                    if self._should_handle_pre_conversation_requirements():
                        resp = self._handle_pre_conversation_requirements()
                        return ChatMessage(content=resp or "")
                else:
                    # If `conversational_consent_enabled` is not enabled, we should just make sure that the session's
                    # status is ACTIVE
                    self.experiment_session.update_status(SessionStatus.ACTIVE)

            enqueue_static_triggers.delay(self.experiment_session.id, StaticTriggerType.NEW_HUMAN_MESSAGE)
            return self._handle_supported_message()
        except Exception as e:
            self._inform_user_of_error(e)
            raise e

    def _handle_pre_conversation_requirements(self) -> str | None:
        """Since external channels doesn't have nice UI, we need to ask users' consent and get them to fill in the
        pre-survey using the conversation thread. We use the session status and a rough state machine to achieve this.

        Here's a breakdown of the flow and the expected session status for each
        Session started -> status will be SETUP
        (Status==SETUP) First user message -> set status to PENDING

        (Status==PENDING) User gave consent -> set status to ACTIVE if there isn't a survey
        (Status==PENDING) User gave consent -> set status to PENDING_PRE_SURVEY if there is a survey

        (Status==PENDING_PRE_SURVEY) user indicated that they took the survey -> sett status to ACTIVE
        """
        # We manually add the message to the history here, since this doesn't follow the normal flow
        self._add_message_to_history(self.user_query, ChatMessageType.HUMAN)

        if self.experiment_session.status == SessionStatus.SETUP:
            return self._chat_initiated()
        elif self.experiment_session.status == SessionStatus.PENDING:
            if self._user_gave_consent():
                if not self.experiment.pre_survey:
                    return self.start_conversation()
                else:
                    self.experiment_session.update_status(SessionStatus.PENDING_PRE_SURVEY)
                    return self._ask_user_to_take_survey()
            else:
                return self._ask_user_for_consent()
        elif self.experiment_session.status == SessionStatus.PENDING_PRE_SURVEY:
            if self._user_gave_consent():
                return self.start_conversation()
            else:
                return self._ask_user_to_take_survey()
        return None

    def start_conversation(self) -> str | None:
        self.experiment_session.update_status(SessionStatus.ACTIVE)
        # This is technically the start of the conversation
        if self.experiment.seed_message:
            return self._send_seed_message()
        return None

    def _send_seed_message(self) -> str:
        with self.trace_service.span("seed_message", inputs={"input": self.experiment.seed_message}) as span:
            bot_response = self.bot.process_input(user_input=self.experiment.seed_message, save_input_to_history=False)
            span.set_outputs({"response": bot_response.content})
            self.send_message_to_user(bot_response.content)
            return bot_response.content

    def _chat_initiated(self):
        """The user initiated the chat and we need to get their consent before continuing the conversation"""
        self.experiment_session.update_status(SessionStatus.PENDING)
        return self._ask_user_for_consent()

    def _ask_user_for_consent(self) -> str:
        consent_text = self.experiment.consent_form.consent_text
        confirmation_text = self.experiment.consent_form.confirmation_text
        bot_message = f"{consent_text}\n\n{confirmation_text}"
        self._add_message_to_history(bot_message, ChatMessageType.AI)
        self.send_text_to_user(bot_message)
        return bot_message

    def _ask_user_to_take_survey(self):
        pre_survey_link = self.experiment_session.get_pre_survey_link(self.experiment)
        confirmation_text = self.experiment.pre_survey.confirmation_text
        bot_message = confirmation_text.format(survey_link=pre_survey_link)
        self._add_message_to_history(bot_message, ChatMessageType.AI)
        self.send_text_to_user(bot_message)
        return bot_message

    def _should_handle_pre_conversation_requirements(self):
        """Checks to see if the user went through the pre-conversation formalities, such as giving consent and filling
        out the survey. Since we're using and updating the session's status during this flow, simply checking the
        session status should be enough.
        """
        return self.experiment_session.status in [
            SessionStatus.SETUP,
            SessionStatus.PENDING,
            SessionStatus.PENDING_PRE_SURVEY,
        ]

    def _user_gave_consent(self) -> bool:
        return (
            self.message
            and self.message.content_type == MESSAGE_TYPES.TEXT
            and self.message.message_text.strip() == USER_CONSENT_TEXT
        )

    def _extract_user_query(self) -> str:
        if self.message.content_type == MESSAGE_TYPES.VOICE:
            return self._get_voice_transcript()
        return self.message.message_text

    def send_message_to_user(self, bot_message: str, files: list[File] | None = None):
        """Sends the `bot_message` to the user. The experiment's config will determine which message type to use"""
        files = files or []
        supported_files = []
        unsupported_files = []

        reply_text = True
        user_sent_voice = self.message and self.message.content_type == MESSAGE_TYPES.VOICE

        if self.voice_replies_supported and self.experiment.synthetic_voice:
            voice_config = self.experiment.voice_response_behaviour
            if voice_config == VoiceResponseBehaviours.ALWAYS or (
                voice_config == VoiceResponseBehaviours.RECIPROCAL and user_sent_voice
            ):
                reply_text = False

        if self.supports_multimedia:
            supported_files, unsupported_files = self._get_supported_unsupported_files(files)
        else:
            unsupported_files = files

        if reply_text:
            bot_message, uncited_files = self._format_reference_section(bot_message, files=files)
            # Cited file links are already included in the bot message, so we only need to append the list of
            # unsupported files that are also uncited
            unsupported_files = [file for file in unsupported_files if file in uncited_files]

            bot_message = self.append_attachment_links(bot_message, linkify_files=unsupported_files)
            self.send_text_to_user(bot_message)
        else:
            bot_message, extracted_urls = strip_urls_and_emojis(bot_message)
            urls_to_append = "\n".join(extracted_urls)
            urls_to_append = self.append_attachment_links(urls_to_append, linkify_files=unsupported_files)

            try:
                self._reply_voice_message(bot_message)

                if urls_to_append:
                    self.send_text_to_user(urls_to_append)
            except AudioSynthesizeException as e:
                logger.exception(e)
                bot_message = f"{bot_message}\n\n{urls_to_append}"

        # Finally send the attachments that are supported by the channel
        if supported_files:
            self._send_files_to_user(supported_files)

    def _format_reference_section(self, text: str, files: list[File]) -> tuple[str, list[File]]:
        """
        Formats file references in text to be channel-appropriate.

        This method processes markdown-style file references in text and adapts them based on
        the channel's file-sending capabilities. It handles both inline citations and reference
        sections at the end of the text.

        Processing steps:
        1. Convert footnote citations [^1] to regular citations [1] for non-web channels
        2. Process reference entries like "[1]: [filename.txt](http://example.com/file.txt)"
        3. For files that CAN be sent through the channel: show only filename
        4. For files that CANNOT be sent: show filename with download link in parentheses

        Args:
            text: The text containing markdown file references
            files: List of File objects that may be referenced in the text

        Returns:
            tuple: (formatted_text, uncited_files)
                - formatted_text: Text with references adapted for the channel
                - uncited_files: Files from the input list that weren't referenced in text

        Example:
            Input text (assuming .txt files can't be sent, .pdf files can):
            ```
            Here's a fact [^1] and another [^2].

            [^1]: [report.txt](http://example.com/report.txt)
            [^2]: [summary.pdf](http://example.com/summary.pdf)
            ```

            Output text:
            ```
            Here's a fact [1] and another [2].

            [1]: report.txt (http://example.com/report.txt)
            [2]: summary.pdf
            ```
        """
        text = re.sub(r"\[\^([^\]]+)\]", r"[\1]", text)

        cited_files = set()
        if not files:
            return text, []

        files_by_name = {file.name: file for file in files}

        def format_citation_match(match):
            ref_id = match.groupdict()["ref"]
            file_name = match.groupdict()["file_name"]
            download_link = match.groupdict()["download_link"]
            file = files_by_name.get(file_name)

            if not file:
                return match.group(0)

            cited_files.add(file)

            if self._can_send_file(file):
                return f"[{ref_id}]: {file.name}"
            else:
                return f"[{ref_id}]: {file.name} ({download_link})"

        markdown_ref_pattern = re.compile(MARKDOWN_REF_PATTERN, re.MULTILINE)
        text = markdown_ref_pattern.sub(format_citation_match, text)

        uncited_files = [file for file in files if file not in cited_files]
        return text, uncited_files

    def _send_files_to_user(self, files: list[File]):
        """
        Try sending each attachment separately. If it fails, send the download link to the user instead.
        """

        for file in files:
            try:
                self.send_file_to_user(file)
            except Exception as e:
                logger.exception(e)
                download_link = file.download_link(self.experiment_session.id)
                self.send_text_to_user(download_link)

    def _handle_supported_message(self):
        with self.trace_service.span("Process Message", inputs={"input": self.user_query}) as span:
            self.submit_input_to_llm()
            ai_message = self._get_bot_response(message=self.user_query)

            files = ai_message.get_attached_files() or []
            span.set_outputs({"response": ai_message.content, "attachments": [file.name for file in files]})

            with self.trace_service.span(
                "Send message to user", inputs={"bot_message": ai_message.content, "files": [str(f) for f in files]}
            ):
                self.send_message_to_user(bot_message=ai_message.content, files=files)

        # Returning the response here is a bit of a hack to support chats through the web UI while trying to
        # use a coherent interface to manage / handle user messages
        return ai_message

    def _handle_unsupported_message(self) -> str:
        response = self._unsupported_message_type_response()
        self.send_text_to_user(response)
        return response

    def _reply_voice_message(self, text: str):
        voice_provider = self.experiment.voice_provider
        synthetic_voice = self.experiment.synthetic_voice
        voice = self.bot.synthesize_voice()
        if voice:
            synthetic_voice = voice

        speech_service = voice_provider.get_speech_service()
        synthetic_voice_audio = speech_service.synthesize_voice(text, synthetic_voice)
        self.send_voice_to_user(synthetic_voice_audio)

    def _get_voice_transcript(self) -> str:
        # Indicate to the user that the bot is busy processing the message
        self.transcription_started()

        audio_file = self.get_message_audio()
        transcript = self._transcribe_audio(audio_file)
        if self.experiment.echo_transcript:
            self.echo_transcript(transcript)
        self.transcription_finished(transcript)
        return transcript

    def _transcribe_audio(self, audio: BytesIO) -> str:
        llm_service = self.experiment.get_llm_service()
        if llm_service and llm_service.supports_transcription:
            return llm_service.transcribe_audio(audio)
        elif self.experiment.voice_provider:
            speech_service = self.experiment.voice_provider.get_speech_service()
            if speech_service.supports_transcription:
                return speech_service.transcribe_audio(audio)
        return "Unable to transcribe audio"

    def _get_bot_response(self, message: str) -> ChatMessage:
        chat_message = self.bot.process_input(message, attachments=self.message.attachments)
        return chat_message

    def _add_message_to_history(self, message: str, message_type: ChatMessageType):
        """Use this to update the chat history when not using the normal bot flow"""
        ChatMessage.objects.create(
            chat=self.experiment_session.chat,
            message_type=message_type,
            content=message,
        )

    def _ensure_sessions_exists(self):
        """
        Ensures an experiment session exists for the given experiment and chat ID.

        Checks if an experiment session already exists for the specified experiment and chat ID.
        If not, a new experiment session is created and associated with the chat.

        If the user requested a new session (by sending the reset command), this will create a new experiment
        session.
        """
        if not self.experiment_session:
            self._load_latest_session()

        if not self.experiment_session:
            self._create_new_experiment_session()
        elif self._is_user_message:
            if self._is_reset_conversation_request() and self.experiment_session.user_already_engaged():
                self._reset_session()
            if not self.experiment_session.experiment_channel:
                # This branch will only be entered for channel sessions that were created by the data migration.
                # These sessions doesn't have experiment channels associated with them, so we need to make sure that
                # they have experiment channels here. For new chats/sessions, the channel is added when they're
                # created in _create_new_experiment_session.
                # See this PR: https://github.com/czue/gpt-playground/pull/67
                # If you see this comment in or after November 2023, you can remove this code. Do update the data
                # migration (apps/channels/migrations/0005_create_channel_sessions.py) to link experiment channels
                # to the channel sessions when removing this code
                self.experiment_session.experiment_channel = self.experiment_channel
                self.experiment_session.save()

    def ensure_session_exists_for_participant(self, identifier: str, new_session: bool = False):
        """
        Ensures an experiment session exists for the participant specied with `identifier`. This is useful for creating
        a session outside of the normal flow where a participant initiates the interaction and where we'll have the
        participant identifier from the incoming messasge. When the bot initiates the conversation, this is not true
        anymore, so we'll need to get the identifier from the params.

        If `new_session` is `True`, the current session will be ended (if one exists) and a new one will be
        created.

        Raises:
            ChannelException when there is an existing session, but with another participant.
        """
        if self.participant_identifier:
            if self.participant_identifier != identifier:
                raise ChannelException("Participant identifier does not match the existing one")
        else:
            self._participant_identifier = identifier

        if new_session:
            self._load_latest_session()
            self._reset_session()
        else:
            self._ensure_sessions_exists()

    def _load_latest_session(self):
        """Loads the latest experiment session on the channel"""
        self.experiment_session = (
            ExperimentSession.objects.filter(
                experiment=self.experiment.get_working_version(),
                participant__identifier=str(self.participant_identifier),
            )
            .exclude(status__in=STATUSES_FOR_COMPLETE_CHATS)
            .order_by("-created_at")
            .first()
        )

    def _reset_session(self):
        """Resets the session by ending the current `experiment_session` (if one exists) and creating a new one"""
        if self.experiment_session:
            self.experiment_session.end()
        self._create_new_experiment_session()

    def _create_new_experiment_session(self):
        """Creates a new experiment session. If one already exists, the participant will be transfered to the new
        session
        """
        self.experiment_session = self.start_new_session(
            working_experiment=self.experiment.get_working_version(),
            experiment_channel=self.experiment_channel,
            participant_identifier=self.participant_identifier,
            participant_user=self.participant_user,
            session_status=SessionStatus.SETUP,
        )

    def _is_reset_conversation_request(self):
        return (
            self.message
            and self.message.content_type == MESSAGE_TYPES.TEXT
            and self.message.message_text.lower().strip() == ExperimentChannel.RESET_COMMAND
        )

    def is_message_type_supported(self) -> bool:
        return self.message and self.message.content_type in self.supported_message_types

    def _unsupported_message_type_response(self) -> str:
        """Generates a suitable response to the user when they send unsupported messages"""
        history_manager = ExperimentHistoryManager(
            session=self.experiment_session, experiment=self.experiment, trace_service=self.trace_service
        )
        trace_info = TraceInfo(name="unsupported message", metadata={"message_type": self.message.content_type})
        chat_message = ChatMessage.objects.create(
            chat=self.experiment_session.chat, message_type=ChatMessageType.AI, content=self.message.message_text
        )
        chat_message.create_and_add_tag("unsupported_message_type", self.experiment.team, TagCategories.ERROR)
        return EventBot(self.experiment_session, self.experiment, trace_info, history_manager).get_user_message(
            UNSUPPORTED_MESSAGE_BOT_PROMPT.format(supported_types=self.supported_message_types)
        )

    def _inform_user_of_error(self, exception):
        """Simply tells the user that something went wrong to keep them in the loop.
        This method will not raise an error if something went wrong during this operation.
        """

        trace_info = TraceInfo(name="error", metadata={"error": str(exception)})
        try:
            bot_message = EventBot(self.experiment_session, self.experiment, trace_info).get_user_message(
                "Tell the user that something went wrong while processing their message and that they should "
                "try again later."
            )
        except Exception:  # noqa BLE001
            logger.exception("Something went wrong while trying to generate an appropriate error message for the user")
            bot_message = DEFAULT_ERROR_RESPONSE_TEXT

        try:
            self.send_message_to_user(bot_message)
        except Exception:  # noqa BLE001
            logger.exception("Something went wrong while trying to inform the user of an error")

    def _get_supported_unsupported_files(self, files: list[File]) -> tuple[list[File], list[File]]:
        """
        Splits the files into two lists based on file size and support by the messaging service:
        1. Files that are both supported by the messaging service and below max_file_size
        2. Files that are either unsupported or above max_file_size (these will be sent as links)

        Returns:
            A tuple of (supported_files, unsupported_files)
        """
        supported_files = []
        unsupported_files = []

        for file in files:
            if self._can_send_file(file):
                supported_files.append(file)
            else:
                unsupported_files.append(file)

        return supported_files, unsupported_files

    def _check_consent(self, strict=True, default_consent=False):
        # This is a failsafe, checks should also happen earlier in the process
        if self.experiment_session:
            try:
                participant_data = self.participant_data
            except ParticipantData.DoesNotExist:
                if strict:
                    raise ChannelException("Participant has not given consent to chat") from None
                else:
                    return

            if not participant_data.system_metadata.get("consent", default_consent):
                raise ChannelException("Participant has not given consent to chat")


class WebChannel(ChannelBase):
    """Message Handler for the UI"""

    voice_replies_supported = False
    supported_message_types = [MESSAGE_TYPES.TEXT]
    supports_conversational_consent_flow: bool = False

    def send_text_to_user(self, bot_message: str):
        # Bot responses are returned by the task and picked up by a periodic request from the browser.
        # Ad-hoc bot messages are picked up by a periodic poll from the browser as well
        pass

    def _ensure_sessions_exists(self):
        if not self.experiment_session:
            raise ChannelException("WebChannel requires an existing session")

    @classmethod
    def start_new_session(
        cls,
        working_experiment: Experiment,
        participant_identifier: str,
        participant_user: CustomUser | None = None,
        session_status: SessionStatus = SessionStatus.ACTIVE,
        timezone: str | None = None,
        version: int = Experiment.DEFAULT_VERSION_NUMBER,
        metadata: dict | None = None,
    ):
        experiment_channel = ExperimentChannel.objects.get_team_web_channel(working_experiment.team)
        session = super().start_new_session(
            working_experiment,
            experiment_channel,
            participant_identifier,
            participant_user,
            session_status,
            timezone,
            metadata=metadata,
        )

        try:
            experiment_version = working_experiment.get_version(version)
            session.chat.set_metadata(Chat.MetadataKeys.EXPERIMENT_VERSION, version)
        except Experiment.DoesNotExist:
            raise Http404(f"Experiment with version {version} not found") from None

        WebChannel.check_and_process_seed_message(session, experiment_version)
        return session

    @classmethod
    def check_and_process_seed_message(cls, session: ExperimentSession, experiment: Experiment):
        from apps.experiments.tasks import get_response_for_webchat_task

        if seed_message := experiment.seed_message:
            session.seed_task_id = get_response_for_webchat_task.delay(
                experiment_session_id=session.id, experiment_id=experiment.id, message_text=seed_message, attachments=[]
            ).task_id
            session.save()
        return session

    def _inform_user_of_error(self, exception):
        # Web channel's errors are optionally rendered in the UI, so no need to send a message
        pass


class TelegramChannel(ChannelBase):
    voice_replies_supported = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]
    supports_multimedia = True

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        self._check_consent(strict=False, default_consent=True)
        self.telegram_bot = TeleBot(self.experiment_channel.extra_data["bot_token"], threaded=False)

    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        self._check_consent(strict=False, default_consent=True)
        try:
            antiflood(
                self.telegram_bot.send_voice,
                self.participant_identifier,
                voice=synthetic_voice.audio,
                duration=synthetic_voice.duration,
            )
        except ApiTelegramException as e:
            self._handle_telegram_api_error(e)

    def send_text_to_user(self, text: str):
        self._check_consent(strict=False, default_consent=True)
        try:
            for message_text in smart_split(text):
                antiflood(self.telegram_bot.send_message, self.participant_identifier, text=message_text)
        except ApiTelegramException as e:
            self._handle_telegram_api_error(e)

    def get_message_audio(self) -> BytesIO:
        file_url = self.telegram_bot.get_file_url(self.message.media_id)
        ogg_audio = BytesIO(requests.get(file_url).content)
        return audio.convert_audio(ogg_audio, target_format="wav", source_format="ogg")

    def _handle_telegram_api_error(self, e: ApiTelegramException):
        if e.error_code == 403 and "bot was blocked by the user" in e.description:
            try:
                participant_data = self.participant_data
                participant_data.update_consent(False)
            except ParticipantData.DoesNotExist:
                raise ChannelException("Participant data does not exist during consent update") from e
            except Exception as e:
                raise ChannelException(f"Unable to update consent for participant {self.participant_identifier}") from e
        else:
            raise ChannelException(f"Telegram API error occurred: {e.description}") from e

    # Callbacks

    def submit_input_to_llm(self):
        # Indicate to the user that the bot is busy processing the message
        self.telegram_bot.send_chat_action(chat_id=self.participant_identifier, action="typing")

    def transcription_started(self):
        self.telegram_bot.send_chat_action(chat_id=self.participant_identifier, action="upload_voice")

    def echo_transcript(self, transcript: str):
        self.telegram_bot.send_message(
            self.participant_identifier, text=f"I heard: {transcript}", reply_to_message_id=self.message.message_id
        )

    def _can_send_file(self, file: File) -> bool:
        mime = file.content_type
        size = file.content_size or 0  # in bytes

        if mime.startswith("image/"):
            return size <= 10 * 1024 * 1024  # 10 MB for images
        elif mime.startswith(("video/", "audio/", "application/")):
            return size <= 50 * 1024 * 1024  # 50 MB for other supported types
        else:
            return False

    def send_file_to_user(self, file: File):
        chat_id = self.participant_identifier
        mime = file.content_type
        file_data = file.file

        main_type = mime.split("/")[0]
        arg_name = ""

        match main_type:
            case "image":
                method = self.telegram_bot.send_photo
                arg_name = "photo"
            case "video":
                method = self.telegram_bot.send_video
                arg_name = "video"
            case "audio":
                method = self.telegram_bot.send_audio
                arg_name = "audio"
            case _:
                method = self.telegram_bot.send_document
                arg_name = "document"

        antiflood(method, chat_id, **{arg_name: file_data})


class WhatsappChannel(ChannelBase):
    @property
    def voice_replies_supported(self) -> bool:
        # TODO: Update turn-python library to support this
        return self.messaging_service.voice_replies_supported

    @property
    def supports_multimedia(self) -> bool:
        return self.messaging_service.supports_multimedia

    @property
    def supported_message_types(self):
        return self.messaging_service.supported_message_types

    def echo_transcript(self, transcript: str):
        self.send_text_to_user(f'I heard: "{transcript}"')

    def send_text_to_user(self, text: str):
        from_number = self.experiment_channel.extra_data["number"]
        to_number = self.participant_identifier

        self.messaging_service.send_text_message(
            message=text, from_=from_number, to=to_number, platform=ChannelPlatform.WHATSAPP
        )

    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        """
        Uploads the synthesized voice to AWS and send the public link to twilio
        """
        from_number = self.experiment_channel.extra_data["number"]
        to_number = self.participant_identifier

        self.messaging_service.send_voice_message(
            synthetic_voice, from_=from_number, to=to_number, platform=ChannelPlatform.WHATSAPP
        )

    def send_file_to_user(self, file: File):
        from_number = self.experiment_channel.extra_data["number"]
        to_number = self.participant_identifier
        self.messaging_service.send_file_to_user(
            from_=from_number,
            to=to_number,
            platform=ChannelPlatform.WHATSAPP,
            file=file,
            download_link=file.download_link(experiment_session_id=self.experiment_session.id),
        )

    def _can_send_file(self, file: File) -> bool:
        return self.messaging_service.can_send_file(file)


class SureAdhereChannel(ChannelBase):
    def send_text_to_user(self, text: str):
        to_patient = self.participant_identifier
        self.messaging_service.send_text_message(message=text, to=to_patient, platform=ChannelPlatform.SUREADHERE)

    @property
    def supported_message_types(self):
        return self.messaging_service.supported_message_types

    @property
    def message_content_type(self):
        return self.message.content_type

    @property
    def message_text(self):
        return self.message.message_text


class FacebookMessengerChannel(ChannelBase):
    def send_text_to_user(self, text: str):
        from_ = self.experiment_channel.extra_data.get("page_id")
        self.messaging_service.send_text_message(
            message=text, from_=from_, to=self.participant_identifier, platform=ChannelPlatform.FACEBOOK
        )

    @property
    def voice_replies_supported(self) -> bool:
        return self.messaging_service.voice_replies_supported

    @property
    def supported_message_types(self):
        return self.messaging_service.supported_message_types

    def echo_transcript(self, transcript: str):
        self.send_text_to_user(f'I heard: "{transcript}"')

    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        """
        Uploads the synthesized voice to AWS and send the public link to twilio
        """
        from_ = self.experiment_channel.extra_data["page_id"]
        self.messaging_service.send_voice_message(
            synthetic_voice, from_=from_, to=self.participant_identifier, platform=ChannelPlatform.FACEBOOK
        )


class ApiChannel(ChannelBase):
    """Message Handler for the API"""

    voice_replies_supported = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
        user=None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        self.user = user
        if not self.user and not self.experiment_session:
            raise ChannelException("ApiChannel requires either an existing session or a user")

    @property
    def participant_user(self):
        return super().participant_user or self.user

    def send_text_to_user(self, bot_message: str):
        # The bot cannot send messages to this client, since it wouldn't know where to send it to
        pass


class SlackChannel(ChannelBase):
    voice_replies_supported = False
    supported_message_types = [MESSAGE_TYPES.TEXT]
    supports_multimedia = True

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession,
        messaging_service=None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        self._messaging_service = messaging_service

    @property
    def messaging_service(self):
        if not self._messaging_service:
            self._messaging_service = self.experiment_channel.messaging_provider.get_messaging_service()
        return self._messaging_service

    def send_text_to_user(self, text: str):
        if not self.message:
            channel_id, thread_ts = parse_session_external_id(self.experiment_session.external_id)
        else:
            channel_id = self.message.channel_id
            thread_ts = self.message.thread_ts
        self.messaging_service.send_text_message(
            text,
            from_="",
            to=channel_id,
            platform=ChannelPlatform.SLACK,
            thread_ts=thread_ts,
        )

    def _ensure_sessions_exists(self):
        if not self.experiment_session:
            raise ChannelException("WebChannel requires an existing session")

    def _can_send_file(self, file: File) -> bool:
        mime = file.content_type
        size = file.content_size or 0
        # slack allows 1 GB, but keeping it to 50MB as we can only upload file upto 50MB in collections
        max_size = settings.MAX_FILE_SIZE_MB * 1024 * 1024
        return mime.startswith(("image/", "video/", "audio/", "application/")) and size <= max_size

    def send_file_to_user(self, file: File):
        if not self.message:
            channel_id, thread_ts = parse_session_external_id(self.experiment_session.external_id)
        else:
            channel_id = self.message.channel_id
            thread_ts = self.message.thread_ts
        self.messaging_service.send_file_message(
            file=file,
            to=channel_id,
            thread_ts=thread_ts,
        )


class CommCareConnectChannel(ChannelBase):
    voice_replies_supported = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        self._check_consent(strict=False)
        self.client = CommCareConnectClient()

    def _ensure_sessions_exists(self):
        super()._ensure_sessions_exists()
        self._check_consent()

    def send_text_to_user(self, text: str):
        self._check_consent()
        self.client.send_message_to_user(
            channel_id=self.connect_channel_id, message=text, encryption_key=self.encryption_key
        )

    @cached_property
    def connect_channel_id(self) -> str:
        channel_id = self.participant_data.system_metadata.get("commcare_connect_channel_id")
        if not channel_id:
            raise ChannelException(f"channel_id is missing for participant {self.participant_identifier}")
        return channel_id

    @cached_property
    def encryption_key(self) -> bytes:
        if not self.participant_data.encryption_key:
            self.participant_data.generate_encryption_key()
        return self.participant_data.get_encryption_key_bytes()


def _start_experiment_session(
    working_experiment: Experiment,
    experiment_channel: ExperimentChannel,
    participant_identifier: str,
    participant_user: CustomUser | None = None,
    session_status: SessionStatus = SessionStatus.ACTIVE,
    timezone: str | None = None,
    session_external_id: str | None = None,
    metadata: dict | None = None,
) -> ExperimentSession:
    if working_experiment.is_a_version:
        raise VersionedExperimentSessionsNotAllowedException(
            message="A session cannot be linked to an experiment version. "
        )

    team = working_experiment.team
    if not participant_identifier and not participant_user:
        raise ValueError("Either participant_identifier or participant_user must be specified!")

    if participant_user and participant_identifier != participant_user.email:
        # This should technically never happen, since we disable the input for logged in users
        raise Exception(f"User {participant_user.email} cannot impersonate participant {participant_identifier}")

    with transaction.atomic():
        participant, created = Participant.objects.get_or_create(
            team=team,
            identifier=experiment_channel.platform_enum.normalize_identifier(participant_identifier),
            platform=experiment_channel.platform,
            defaults={"user": participant_user},
        )
        if not created and participant_user and participant.user is None:
            participant.user = participant_user
            participant.save()

        chat = Chat.objects.create(
            team=team,
            name=f"{participant_identifier} - {experiment_channel.name}",
            metadata=metadata or {},
        )

        session, _ = ExperimentSession.objects.get_or_create(
            external_id=session_external_id,
            defaults={
                "team": team,
                "experiment": working_experiment,
                "experiment_channel": experiment_channel,
                "status": session_status,
                "participant": participant,
                "chat": chat,
            },
        )

        # Record the participant's timezone
        if timezone:
            participant.update_memory(data={"timezone": timezone}, experiment=working_experiment)

    if participant.experimentsession_set.filter(experiment=working_experiment).count() == 1:
        enqueue_static_triggers.delay(session.id, StaticTriggerType.PARTICIPANT_JOINED_EXPERIMENT)
    enqueue_static_triggers.delay(session.id, StaticTriggerType.CONVERSATION_START)
    return session


class EvaluationChannel(ChannelBase):
    """Message Handler for Evaluations"""

    voice_replies_supported = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession,
        participant_data: dict,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        if not self.experiment_session:
            raise ChannelException("EvaluationChannel requires an existing session")
        self._participant_data = participant_data

        self.trace_service = TracingService.empty()

    def send_text_to_user(self, bot_message: str):
        # The bot cannot send messages to this client, since evaluations are run internally
        pass

    @property
    def bot(self):
        return EvalsBot(
            self.experiment_session,
            self.experiment,
            self.trace_service,
            participant_data=self._participant_data,
        )
