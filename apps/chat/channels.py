import logging
import re
from abc import ABC, abstractmethod
from enum import Enum
from functools import cached_property
from io import BytesIO
from typing import ClassVar

import emoji
import requests
from django.db import transaction
from django.http import Http404
from telebot import TeleBot
from telebot.util import antiflood, smart_split

from apps.channels import audio
from apps.channels.clients.connect_client import CommCareConnectClient
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.bots import get_bot
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
from apps.service_providers.llm_service.runnables import GenerationCancelled
from apps.service_providers.speech_service import SynthesizedAudio
from apps.slack.utils import parse_session_external_id
from apps.users.models import CustomUser

logger = logging.getLogger("ocs.channels")

USER_CONSENT_TEXT = "1"
UNSUPPORTED_MESSAGE_BOT_PROMPT = """
Tell the user (in the language being spoken) that they sent an unsupported message.
You only support {supported_types} messages types. Respond only with the message for the user
"""
DEFAULT_ERROR_RESPONSE_TEXT = "Sorry, something went wrong while processing your message. Please try again later"

# The regex from https://stackoverflow.com/a/6041965 is used, but tweaked to remove capturing groups
URL_REGEX = r"(?:http|ftp|https):\/\/(?:[\w_-]+(?:(?:\.[\w_-]+)+))(?:[\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-])"


def strip_urls_and_emojis(text: str) -> tuple[str, list[str]]:
    """Strips any URLs in `text` and appends them to the end of the text. Emoji's are filtered out"""
    text = emoji.replace_emoji(text, replace="")

    url_pattern = re.compile(URL_REGEX)
    urls = set(url_pattern.findall(text))
    for url in urls:
        text = text.replace(url, "")

    return text, urls


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

    Attributes:
        voice_replies_supported: Indicates whether the channel supports voice messages

    Args:
        experiment: An Experiment object representing the experiment associated with the handler.
        experiment_channel: An ExperimentChannel object representing the channel associated with the handler.
        experiment_session: An optional ExperimentSession object representing the experiment session associated
            with the handler.
    Raises:
        ChannelException: If both 'experiment_channel' and 'experiment_session' arguments are not provided.

    Class variables:
        supported_message_types: A list of message content types that are supported by this channel

    Abstract methods:
        send_voice_to_user: (Optional) An abstract method to send a voice message to the user. This must be implemented
            if voice_replies_supported is True
        send_text_to_user: Implementation of sending text to the user. Typically this is the reply from the bot
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

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        self.experiment = experiment
        self.experiment_channel = experiment_channel
        self.experiment_session = experiment_session
        self.message = None
        self._user_query = None
        self.bot = get_bot(experiment_session, experiment=experiment) if experiment_session else None
        self._participant_identifier = experiment_session.participant.identifier if experiment_session else None
        self._is_user_message = False

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
    ):
        return _start_experiment_session(
            working_experiment,
            experiment_channel,
            participant_identifier,
            participant_user,
            session_status,
            timezone,
            session_external_id,
        )

    @cached_property
    def messaging_service(self):
        return self.experiment_channel.messaging_provider.get_messaging_service()

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

    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        raise NotImplementedError(
            "Voice replies are supported but the method reply (`send_voice_to_user`) is not implemented"
        )

    @abstractmethod
    def send_text_to_user(self, text: str):
        """Channel specific way of sending text back to the user"""
        raise NotImplementedError()

    def get_message_audio(self) -> BytesIO:
        return self.messaging_service.get_message_audio(message=self.message)

    def echo_transcript(self, transcript: str):
        """Sends a text message to the user with a transcript of what the user said"""
        pass

    def transcription_started(self):
        """Callback indicating that the transcription process started"""
        pass

    def transcription_finished(self, transcript: str):
        """Callback indicating that the transcription is finished"""
        pass

    def submit_input_to_llm(self):
        """Callback indicating that the user input will now be given to the LLM"""
        pass

    @staticmethod
    def get_channel_class_for_platform(platform: ChannelPlatform) -> "ChannelBase":
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
        else:
            raise Exception(f"Unsupported platform type {platform}")
        return channel_cls

    @staticmethod
    def from_experiment_session(experiment_session: ExperimentSession) -> "ChannelBase":
        """Given an `experiment_session` instance, returns the correct ChannelBase subclass to use"""
        channel_cls = ChannelBase.get_channel_class_for_platform(experiment_session.experiment_channel.platform)
        return channel_cls(
            experiment_session.experiment,
            experiment_channel=experiment_session.experiment_channel,
            experiment_session=experiment_session,
        )

    @property
    def user_query(self):
        """Returns the user query, extracted from whatever (supported) message type was used to convey the
        message
        """
        if not self._user_query:
            self._user_query = self._extract_user_query()
        return self._user_query

    def _add_message(self, message):
        """Adds the message to the handler in order to extract session information"""
        self._user_query = None
        self.message = message

        if not self._participant_is_allowed():
            raise ParticipantNotAllowedException()

        self._ensure_sessions_exists()
        self.bot = get_bot(self.experiment_session, experiment=self.experiment)

    def new_user_message(self, message) -> str:
        """Handles the message coming from the user. Call this to send bot messages to the user.
        The `message` here will probably be some object, depending on the channel being used.
        """
        self._is_user_message = True
        try:
            return self._new_user_message(message)
        except GenerationCancelled:
            return ""

    def _participant_is_allowed(self):
        if self.experiment.is_public:
            return True
        return self.experiment.is_participant_allowed(self.participant_identifier)

    def _new_user_message(self, message) -> str:
        try:
            self._add_message(message)
        except ParticipantNotAllowedException:
            return self.send_message_to_user("Sorry, you are not allowed to chat to this bot")

        try:
            if not self.is_message_type_supported():
                return self._handle_unsupported_message()

            if self.experiment_channel.platform != ChannelPlatform.WEB:
                if self._is_reset_conversation_request():
                    # Webchats' statuses are updated through an "external" flow
                    return ""

                if self.experiment.conversational_consent_enabled and self.experiment.consent_form_id:
                    if self._should_handle_pre_conversation_requirements():
                        self._handle_pre_conversation_requirements()
                        return ""
                else:
                    # If `conversational_consent_enabled` is not enabled, we should just make sure that the session's
                    # status is ACTIVE
                    self.experiment_session.update_status(SessionStatus.ACTIVE)

            enqueue_static_triggers.delay(self.experiment_session.id, StaticTriggerType.NEW_HUMAN_MESSAGE)
            response = self._handle_supported_message()
            return response
        except Exception as e:
            self._inform_user_of_error()
            raise e

    def _handle_pre_conversation_requirements(self):
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
            self._chat_initiated()
        elif self.experiment_session.status == SessionStatus.PENDING:
            if self._user_gave_consent():
                if not self.experiment.pre_survey:
                    self.start_conversation()
                else:
                    self.experiment_session.update_status(SessionStatus.PENDING_PRE_SURVEY)
                    self._ask_user_to_take_survey()
            else:
                self._ask_user_for_consent()
        elif self.experiment_session.status == SessionStatus.PENDING_PRE_SURVEY:
            if self._user_gave_consent():
                self.start_conversation()
            else:
                self._ask_user_to_take_survey()

    def start_conversation(self):
        self.experiment_session.update_status(SessionStatus.ACTIVE)
        # This is technically the start of the conversation
        if self.experiment.seed_message:
            bot_response = self._generate_response_for_user(self.experiment.seed_message)
            self.send_message_to_user(bot_response)

    def _chat_initiated(self):
        """The user initiated the chat and we need to get their consent before continuing the conversation"""
        self.experiment_session.update_status(SessionStatus.PENDING)
        self._ask_user_for_consent()

    def _ask_user_for_consent(self):
        consent_text = self.experiment.consent_form.consent_text
        confirmation_text = self.experiment.consent_form.confirmation_text
        bot_message = f"{consent_text}\n\n{confirmation_text}"
        self._add_message_to_history(bot_message, ChatMessageType.AI)
        self.send_text_to_user(bot_message)

    def _ask_user_to_take_survey(self):
        pre_survey_link = self.experiment_session.get_pre_survey_link(self.experiment)
        confirmation_text = self.experiment.pre_survey.confirmation_text
        bot_message = confirmation_text.format(survey_link=pre_survey_link)
        self._add_message_to_history(bot_message, ChatMessageType.AI)
        self.send_text_to_user(bot_message)

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
        return self.user_query.strip() == USER_CONSENT_TEXT

    def _extract_user_query(self) -> str:
        if self.message.content_type == MESSAGE_TYPES.VOICE:
            return self._get_voice_transcript()
        return self.message.message_text

    def send_message_to_user(self, bot_message: str):
        """Sends the `bot_message` to the user. The experiment's config will determine which message type to use"""
        send_message_func = self.send_text_to_user
        user_sent_voice = self.message and self.message.content_type == MESSAGE_TYPES.VOICE

        if self.voice_replies_supported and self.experiment.synthetic_voice:
            voice_config = self.experiment.voice_response_behaviour
            if voice_config == VoiceResponseBehaviours.ALWAYS:
                send_message_func = self._reply_voice_message
            elif voice_config == VoiceResponseBehaviours.RECIPROCAL and user_sent_voice:
                send_message_func = self._reply_voice_message

        send_message_func(bot_message)

    def _handle_supported_message(self):
        self.submit_input_to_llm()
        response = self._get_bot_response(message=self.user_query)
        self.send_message_to_user(response)
        # Returning the response here is a bit of a hack to support chats through the web UI while trying to
        # use a coherent interface to manage / handle user messages
        return response

    def _handle_unsupported_message(self):
        return self.send_text_to_user(self._unsupported_message_type_response())

    def _reply_voice_message(self, text: str):
        text, extracted_urls = strip_urls_and_emojis(text)

        voice_provider = self.experiment.voice_provider
        synthetic_voice = self.experiment.synthetic_voice
        if self.experiment.use_processor_bot_voice and (
            self.bot.processor_experiment and self.bot.processor_experiment.voice_provider
        ):
            voice_provider = self.bot.processor_experiment.voice_provider
            synthetic_voice = self.bot.processor_experiment.synthetic_voice

        speech_service = voice_provider.get_speech_service()
        try:
            synthetic_voice_audio = speech_service.synthesize_voice(text, synthetic_voice)
            self.send_voice_to_user(synthetic_voice_audio)
        except AudioSynthesizeException as e:
            logger.exception(e)
            self.send_text_to_user(text)

        if extracted_urls:
            urls_text = "\n".join(extracted_urls)
            self.send_text_to_user(urls_text)

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
        if llm_service.supports_transcription:
            return llm_service.transcribe_audio(audio)
        elif self.experiment.voice_provider:
            speech_service = self.experiment.voice_provider.get_speech_service()
            if speech_service.supports_transcription:
                return speech_service.transcribe_audio(audio)

    def _get_bot_response(self, message: str) -> str:
        self.bot = self.bot or get_bot(self.experiment_session, experiment=self.experiment)
        return self.bot.process_input(message, attachments=self.message.attachments)

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
        return self.user_query.lower().strip() == ExperimentChannel.RESET_COMMAND

    def is_message_type_supported(self) -> bool:
        return self.message.content_type is not None and self.message.content_type in self.supported_message_types

    def _unsupported_message_type_response(self):
        """Generates a suitable response to the user when they send unsupported messages"""
        ChatMessage.objects.create(
            chat=self.experiment_session.chat,
            message_type=ChatMessageType.SYSTEM,
            content=f"The user sent an unsupported message type: {self.message.content_type_unparsed}",
        )
        return self._generate_response_for_user(
            UNSUPPORTED_MESSAGE_BOT_PROMPT.format(supported_types=self.supported_message_types)
        )

    def _inform_user_of_error(self):
        """Simply tells the user that something went wrong to keep them in the loop. This method wil not raise an error
        if something went wrong during this operation.
        """

        try:
            bot_message = self._generate_response_for_user(
                """
                Tell the user that something went wrong while processing their message and that they should
                try again later
                """
            )
        except Exception:
            logger.exception("Something went wrong while trying to generate an appropriate error message for the user")
            bot_message = DEFAULT_ERROR_RESPONSE_TEXT

        try:
            self.send_message_to_user(bot_message)
        except Exception:
            logger.exception("Something went wrong while trying to inform the user of an error")

    def _generate_response_for_user(self, prompt: str) -> str:
        """Generates a response based on the `prompt`."""
        topic_bot = self.bot or get_bot(self.experiment_session, experiment=self.experiment)
        return topic_bot.process_input(user_input=prompt, save_input_to_history=False)


class WebChannel(ChannelBase):
    """Message Handler for the UI"""

    voice_replies_supported = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    def send_text_to_user(self, bot_message: str):
        # Simply adding a new AI message to the chat history will cause it to be sent to the UI
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
    ):
        experiment_channel = ExperimentChannel.objects.get_team_web_channel(working_experiment.team)
        session = super().start_new_session(
            working_experiment, experiment_channel, participant_identifier, participant_user, session_status, timezone
        )

        try:
            experiment_version = working_experiment.get_version(version)
            session.chat.set_metadata(Chat.MetadataKeys.EXPERIMENT_VERSION, version)
        except Experiment.DoesNotExist:
            raise Http404(f"Experiment with version {version} not found")

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

    def _inform_user_of_error(self):
        # Web channel's errors are optionally rendered in the UI, so no need to send a message
        pass


class TelegramChannel(ChannelBase):
    voice_replies_supported = True
    supported_message_types = [MESSAGE_TYPES.TEXT, MESSAGE_TYPES.VOICE]

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        super().__init__(experiment, experiment_channel, experiment_session)
        self.telegram_bot = TeleBot(self.experiment_channel.extra_data["bot_token"], threaded=False)

    def send_voice_to_user(self, synthetic_voice: SynthesizedAudio):
        antiflood(
            self.telegram_bot.send_voice,
            self.participant_identifier,
            voice=synthetic_voice.audio,
            duration=synthetic_voice.duration,
        )

    def send_text_to_user(self, text: str):
        for message_text in smart_split(text):
            antiflood(self.telegram_bot.send_message, self.participant_identifier, text=message_text)

    def get_message_audio(self) -> BytesIO:
        file_url = self.telegram_bot.get_file_url(self.message.media_id)
        ogg_audio = BytesIO(requests.get(file_url).content)
        return audio.convert_audio(ogg_audio, target_format="wav", source_format="ogg")

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


class WhatsappChannel(ChannelBase):
    def send_text_to_user(self, text: str):
        from_number = self.experiment_channel.extra_data.get("number")
        to_number = self.participant_identifier
        self.messaging_service.send_text_message(
            text, from_=from_number, to=to_number, platform=ChannelPlatform.WHATSAPP
        )

    def get_chat_id_from_message(self, message):
        return message.chat_id

    @property
    def voice_replies_supported(self) -> bool:
        # TODO: Update turn-python library to support this
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
        from_number = self.experiment_channel.extra_data["number"]
        to_number = self.participant_identifier
        self.messaging_service.send_voice_message(
            synthetic_voice, from_=from_number, to=to_number, platform=ChannelPlatform.WHATSAPP
        )


class SureAdhereChannel(ChannelBase):
    def send_text_to_user(self, text: str):
        to_patient = self.participant_identifier
        self.messaging_service.send_text_message(text, to=to_patient, platform=ChannelPlatform.SUREADHERE)

    def get_chat_id_from_message(self, message):
        return message.chat_id

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
            text, from_=from_, to=self.participant_identifier, platform=ChannelPlatform.FACEBOOK
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

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession,
        send_response_to_user: bool = True,
    ):
        """
        Args:
            send_response_to_user: A boolean indicating whether the handler should send the response to the user.
                This is useful when the message sending happens as part of the slack event handler
                (e.g., in a slack event listener)
        """
        super().__init__(experiment, experiment_channel, experiment_session)
        self.send_response_to_user = send_response_to_user

    def send_text_to_user(self, text: str):
        if not self.send_response_to_user:
            return

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
        self.client = CommCareConnectClient()

    def send_text_to_user(self, text: str):
        if self.participant_data.system_metadata.get("consent", False) is False:
            raise ChannelException("Participant has not given consent to chat")
        self.client.send_message_to_user(
            channel_id=self.connect_channel_id, message=text, encryption_key=self.encryption_key
        )

    @cached_property
    def participant_data(self) -> ParticipantData:
        experiment = self.experiment
        if self.experiment.is_a_version:
            experiment = self.experiment.working_version
        return experiment.participantdata_set.defer("data").get(participant__identifier=self.participant_identifier)

    @cached_property
    def connect_channel_id(self) -> str:
        channel_id = self.participant_data.system_metadata.get("commcare_connect_channel_id")
        if not channel_id:
            raise ChannelException(f"channel_id is missing for participant {self.participant_identifier}")
        return channel_id

    @cached_property
    def encryption_key(self) -> bytes:
        key = self.participant_data.get_encryption_key_bytes()
        if not key:
            raise ChannelException(f"Encryption key is missing for participant {self.participant_identifier}")
        return key


def _start_experiment_session(
    working_experiment: Experiment,
    experiment_channel: ExperimentChannel,
    participant_identifier: str,
    participant_user: CustomUser | None = None,
    session_status: SessionStatus = SessionStatus.ACTIVE,
    timezone: str | None = None,
    session_external_id: str | None = None,
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
            identifier=participant_identifier,
            platform=experiment_channel.platform,
            defaults={"user": participant_user},
        )
        if not created and participant_user and participant.user is None:
            participant.user = participant_user
            participant.save()

        session = ExperimentSession.objects.create(
            team=team,
            experiment=working_experiment,
            experiment_channel=experiment_channel,
            status=session_status,
            participant=participant,
            external_id=session_external_id,
        )

        # Record the participant's timezone
        if timezone:
            participant.update_memory(data={"timezone": timezone}, experiment=working_experiment)

    if participant.experimentsession_set.count() == 1:
        enqueue_static_triggers.delay(session.id, StaticTriggerType.PARTICIPANT_JOINED_EXPERIMENT)
    enqueue_static_triggers.delay(session.id, StaticTriggerType.CONVERSATION_START)
    return session
