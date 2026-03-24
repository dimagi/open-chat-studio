from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import Http404

from apps.channels.channels_v2.api_channel import NoOpSender
from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.pipeline import MessageProcessingPipeline
from apps.channels.channels_v2.stages.core import (
    BotInteractionStage,
    ChatMessageCreationStage,
    MessageTypeValidationStage,
    ParticipantValidationStage,
    QueryExtractionStage,
    ResponseFormattingStage,
    SessionActivationStage,
)
from apps.channels.channels_v2.stages.terminal import ActivityTrackingStage, PersistenceStage
from apps.channels.models import ExperimentChannel
from apps.chat.channels import MESSAGE_TYPES, _start_experiment_session
from apps.chat.exceptions import ChannelException
from apps.chat.models import Chat
from apps.experiments.models import Experiment, SessionStatus

if TYPE_CHECKING:
    from apps.experiments.models import ExperimentSession
    from apps.users.models import CustomUser


class WebChannel(ChannelBase):
    """Message handler for the web UI.

    No message sending, no conversational consent. Responses are returned
    by new_user_message() and picked up by periodic polling from the browser.
    Session is always pre-set (created by start_new_session class method
    before the pipeline runs).

    start_new_session() and check_and_process_seed_message() are class
    methods used outside the pipeline (from web views) and remain on
    the channel class unchanged.
    """

    voice_replies_supported = False
    supported_message_types = [MESSAGE_TYPES.TEXT]

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
    ):
        if not experiment_session:
            raise ChannelException("WebChannel requires an existing session")
        super().__init__(experiment, experiment_channel, experiment_session)

    def _get_sender(self) -> NoOpSender:
        return NoOpSender()

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=False,
            supports_files=False,
            supports_conversational_consent=False,
            supported_message_types=[MESSAGE_TYPES.TEXT],
        )

    def _build_pipeline(self) -> MessageProcessingPipeline:
        return MessageProcessingPipeline(
            core_stages=[
                ParticipantValidationStage(),
                # No SessionResolutionStage — session always pre-set
                SessionActivationStage(),
                MessageTypeValidationStage(),
                # No ConsentFlowStage — web uses UI-based consent
                QueryExtractionStage(),
                ChatMessageCreationStage(),
                BotInteractionStage(),
                ResponseFormattingStage(),
            ],
            terminal_stages=[
                # No ResponseSendingStage or SendingErrorHandlerStage —
                # responses returned via new_user_message()
                PersistenceStage(),
                ActivityTrackingStage(),
            ],
        )

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
        **kwargs,
    ):
        experiment_channel = ExperimentChannel.objects.get_team_web_channel(working_experiment.team)
        session = _start_experiment_session(
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
    def check_and_process_seed_message(cls, session, experiment):
        from apps.experiments.tasks import (  # noqa: PLC0415 - circular: experiments.tasks imports channels
            get_response_for_webchat_task,
        )

        if seed_message := experiment.seed_message:
            session.seed_task_id = get_response_for_webchat_task.delay(
                experiment_session_id=session.id, experiment_id=experiment.id, message_text=seed_message, attachments=[]
            ).task_id
            session.save()
        return session
