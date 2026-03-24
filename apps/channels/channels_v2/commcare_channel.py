from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.exceptions import EarlyExitResponse
from apps.channels.channels_v2.pipeline import MessageProcessingPipeline
from apps.channels.channels_v2.sender import ChannelSender
from apps.channels.channels_v2.stages.base import ProcessingStage
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
from apps.channels.channels_v2.stages.terminal import (
    ActivityTrackingStage,
    PersistenceStage,
    ResponseSendingStage,
    SendingErrorHandlerStage,
)
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.exceptions import ChannelException

if TYPE_CHECKING:
    from apps.channels.channels_v2.pipeline import MessageProcessingContext
    from apps.files.models import File
    from apps.service_providers.speech_service import SynthesizedAudio


class CommCareConsentCheckStage(ProcessingStage):
    """Checks CommCare Connect platform-specific consent.

    Separate from ConsentFlowStage -- this checks system_metadata["consent"]
    on ParticipantData, not the conversational consent state machine.
    """

    def should_run(self, ctx: MessageProcessingContext) -> bool:
        return ctx.experiment_session is not None

    def process(self, ctx: MessageProcessingContext) -> None:
        from apps.experiments.models import ParticipantData  # noqa: PLC0415

        try:
            participant_data = ParticipantData.objects.get(
                participant__identifier=ctx.participant_identifier,
                experiment=ctx.experiment,
            )
        except ParticipantData.DoesNotExist:
            raise EarlyExitResponse("Participant has not given consent to chat") from None

        if not participant_data.system_metadata.get("consent", False):
            raise EarlyExitResponse("Participant has not given consent to chat")


class CommCareConnectSender(ChannelSender):
    """Late-binding sender for CommCare Connect (visitor pattern).

    Holds a reference to the channel instance and resolves
    connect_channel_id / encryption_key lazily on first send.
    By the time send_text is called (in terminal ResponseSendingStage),
    the session exists and participant_data is resolvable.
    """

    def __init__(self, channel: CommCareConnectChannel):
        from apps.channels.clients.connect_client import CommCareConnectClient  # noqa: PLC0415

        self._channel = channel
        self._client = CommCareConnectClient()

    def send_text(self, text: str, recipient: str) -> None:
        self._client.send_message_to_user(
            channel_id=self._channel.connect_channel_id,
            message=text,
            encryption_key=self._channel.encryption_key,
        )

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        raise NotImplementedError

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        raise NotImplementedError


class CommCareConnectChannel(ChannelBase):
    """CommCare Connect channel -- adds platform-specific consent check.

    Overrides _build_pipeline to insert CommCareConsentCheckStage
    after SessionActivationStage.
    """

    voice_replies_supported = False
    supported_message_types = (MESSAGE_TYPES.TEXT,)

    def _build_pipeline(self) -> MessageProcessingPipeline:
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                SessionResolutionStage(),
                SessionActivationStage(),
                CommCareConsentCheckStage(),  # Platform-specific consent
                MessageTypeValidationStage(),
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                ConsentFlowStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                ResponseSendingStage(),
                SendingErrorHandlerStage(),
                PersistenceStage(),
                ActivityTrackingStage(),
            ],
        )

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()  # All no-ops

    def _get_sender(self) -> ChannelSender:
        return CommCareConnectSender(self)

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=False,
            supports_files=False,
            supports_conversational_consent=True,
            supported_message_types=(MESSAGE_TYPES.TEXT,),
        )

    @cached_property
    def connect_channel_id(self) -> str:
        channel_id = self.participant_data.system_metadata.get("commcare_connect_channel_id")
        if not channel_id:
            raise ChannelException(
                f"channel_id is missing for participant {self.experiment_session.participant.identifier}"
            )
        return channel_id

    @cached_property
    def encryption_key(self) -> bytes:
        if not self.participant_data.encryption_key:
            self.participant_data.generate_encryption_key()
        return self.participant_data.get_encryption_key_bytes()

    @cached_property
    def participant_data(self):
        from apps.experiments.models import ParticipantData  # noqa: PLC0415

        experiment = self.experiment
        if self.experiment.is_a_version:
            experiment = self.experiment.working_version
        return ParticipantData.objects.get(
            participant__identifier=self.experiment_session.participant.identifier,
            experiment=experiment,
        )
