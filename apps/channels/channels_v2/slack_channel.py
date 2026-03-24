from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

from apps.channels.channels_v2.callbacks import ChannelCallbacks
from apps.channels.channels_v2.capabilities import ChannelCapabilities
from apps.channels.channels_v2.channel_base import ChannelBase
from apps.channels.channels_v2.sender import ChannelSender
from apps.chat.channels import MESSAGE_TYPES
from apps.chat.exceptions import ChannelException

if TYPE_CHECKING:
    from apps.channels.models import ExperimentChannel
    from apps.experiments.models import Experiment, ExperimentSession
    from apps.files.models import File
    from apps.service_providers.speech_service import SynthesizedAudio


class SlackSender(ChannelSender):
    """Sends messages via the Slack messaging service.

    The channel_id and thread_ts are baked in at construction time
    (from the session's external_id). The messaging service handles
    the actual Slack API calls.
    """

    def __init__(self, messaging_service, channel_id: str, thread_ts: str):
        self._service = messaging_service
        self._channel_id = channel_id
        self._thread_ts = thread_ts

    def send_text(self, text: str, recipient: str) -> None:
        from apps.channels.models import ChannelPlatform  # noqa: PLC0415

        self._service.send_text_message(
            text,
            from_="",
            to=self._channel_id,
            platform=ChannelPlatform.SLACK,
            thread_ts=self._thread_ts,
        )

    def send_voice(self, audio: SynthesizedAudio, recipient: str) -> None:
        raise NotImplementedError

    def send_file(self, file: File, recipient: str, session_id: int) -> None:
        self._service.send_file_message(
            file=file,
            to=self._channel_id,
            thread_ts=self._thread_ts,
        )


class SlackChannel(ChannelBase):
    """Slack channel -- pre-set session, thread-based messaging,
    supports file attachments, no voice.

    Session is always pre-set (created by the Slack event handler before
    the pipeline runs). The channel_id and thread_ts for sending are
    derived from the session's external_id.
    """

    voice_replies_supported = False
    supports_multimedia = True
    supported_message_types = (MESSAGE_TYPES.TEXT,)

    def __init__(
        self,
        experiment: Experiment,
        experiment_channel: ExperimentChannel,
        experiment_session: ExperimentSession | None = None,
        messaging_service=None,
    ):
        if not experiment_session:
            raise ChannelException("SlackChannel requires an existing session")
        super().__init__(experiment, experiment_channel, experiment_session)
        self._messaging_service = messaging_service

    @property
    def messaging_service(self):
        if not self._messaging_service:
            self._messaging_service = self.experiment_channel.messaging_provider.get_messaging_service()
        return self._messaging_service

    def _get_sender(self) -> ChannelSender:
        """Build a sender scoped to the correct Slack channel + thread.

        channel_id/thread_ts come from the session's external_id.
        """
        from apps.slack.utils import parse_session_external_id  # noqa: PLC0415

        channel_id, thread_ts = parse_session_external_id(self.experiment_session.external_id)
        return SlackSender(self.messaging_service, channel_id, thread_ts)

    def _get_callbacks(self) -> ChannelCallbacks:
        return ChannelCallbacks()  # All no-ops -- Slack has no typing indicators or transcript echo

    def _get_capabilities(self) -> ChannelCapabilities:
        return ChannelCapabilities(
            supports_voice_replies=False,
            supports_files=True,
            supports_conversational_consent=True,
            supported_message_types=(MESSAGE_TYPES.TEXT,),
            can_send_file=self._can_send_file,
        )

    def _can_send_file(self, file) -> bool:
        mime = file.content_type
        size = file.content_size or 0
        max_size = settings.MAX_FILE_SIZE_MB * 1024 * 1024
        return mime.startswith(("image/", "video/", "audio/", "application/")) and size <= max_size

    @classmethod
    def start_new_session(
        cls,
        working_experiment,
        experiment_channel,
        participant_identifier,
        participant_user=None,
        session_status=None,
        timezone=None,
        session_external_id=None,
        metadata=None,
    ):
        from apps.chat.channels import _start_experiment_session  # noqa: PLC0415
        from apps.experiments.models import SessionStatus  # noqa: PLC0415

        if session_status is None:
            session_status = SessionStatus.SETUP
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
