from __future__ import annotations

from typing import TYPE_CHECKING

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.pipeline import MessageProcessingContext, MessageProcessingPipeline
from apps.channels.channels_v2.sender import ChannelSender
from apps.channels.channels_v2.stages.core import (
    BotInteractionStage,
    ChatMessageCreationStage,
    ConsentFlowStage,
    MessageTypeValidationStage,
    ParticipantValidationStage,
    QueryExtractionStage,
    ResponseFormattingStage,
    SessionActivationStage,
    SessionResolutionStage,
)
from apps.channels.channels_v2.stages.terminal import ActivityTrackingStage, PersistenceStage
from apps.chat.channels import MESSAGE_TYPES, _start_experiment_session
from apps.chat.exceptions import ChannelException
from apps.chat.models import Chat
from apps.experiments.models import Experiment, SessionStatus

if TYPE_CHECKING:
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import ExperimentSession


class NoOpSender(ChannelSender):
    """Sender that does nothing — API channels return responses directly to the caller."""

    def send_text(self, text, recipient):
        pass

    def send_voice(self, audio, recipient):
        pass

    def send_file(self, file, recipient, session_id):
        pass


class ApiChannel(ChannelBase):
    """Message handler for the API.

    No voice, no files, no sending stages. Responses are returned
    to the caller via the return value of new_user_message().
    """

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

    def _create_context(self, message) -> MessageProcessingContext:
        ctx = super()._create_context(message)
        if self.user:
            ctx.channel_context["participant_user"] = self.user
        return ctx

    def _get_sender(self) -> ChannelSender:
        return NoOpSender()

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=False,
            supports_files=False,
            supports_conversational_consent=True,
            supported_message_types=[MESSAGE_TYPES.TEXT],
        )

    def _build_pipeline(self) -> MessageProcessingPipeline:
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                SessionResolutionStage(),
                SessionActivationStage(),
                MessageTypeValidationStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                ConsentFlowStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                PersistenceStage(),
                ActivityTrackingStage(),
            ],
        )

    @classmethod
    def start_new_session(
        cls,
        working_experiment,
        experiment_channel,
        participant_identifier,
        participant_user=None,
        session_status=SessionStatus.ACTIVE,
        timezone=None,
        session_external_id=None,
        metadata=None,
        version=Experiment.DEFAULT_VERSION_NUMBER,
    ):
        session = _start_experiment_session(
            working_experiment,
            experiment_channel,
            participant_identifier,
            participant_user,
            session_status,
            timezone,
            session_external_id,
            metadata,
        )
        if version != Experiment.DEFAULT_VERSION_NUMBER:
            session.chat.set_metadata(Chat.MetadataKeys.EXPERIMENT_VERSION, version)
        return session

    @property
    def participant_user(self):
        if self.experiment_session:
            return self.experiment_session.participant.user
        return self.user
