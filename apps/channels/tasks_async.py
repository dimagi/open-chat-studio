import logging

from apps.channels.datamodels import BaseMessage
from apps.channels.models import ExperimentChannel
from apps.chat.channels import (
    ApiChannel,
)
from apps.chat.models import ChatMessage
from apps.experiments.models import Experiment, ExperimentSession
from apps.users.models import CustomUser
from config.tkq import broker

log = logging.getLogger("ocs.channels")


@broker.task
async def handle_api_message_async(
    user_id: int,
    experiment_version_id: int,
    experiment_channel_id: int,
    message_text: str,
    participant_id: str,
    session_id: int,
):
    user = await CustomUser.objects.aget(id=user_id) if user_id else None
    experiment_version = await Experiment.objects.select_related("team", "pipeline", "trace_provider").aget(
        id=experiment_version_id
    )
    experiment_channel = await ExperimentChannel.objects.aget(id=experiment_channel_id)
    session = None
    if session_id:
        session = await ExperimentSession.objects.select_related(
            "experiment_channel", "experiment", "participant", "experiment__team"
        ).aget(id=session_id)

    message = await ahandle_api_message(
        user, experiment_version, experiment_channel, message_text, participant_id, session
    )
    return message.content


async def ahandle_api_message(
    user, experiment_version, experiment_channel, message_text: str, participant_id: str, session=None
) -> ChatMessage:
    """Asynchronously handles the message coming from the API."""
    message = BaseMessage(participant_id=participant_id, message_text=message_text)

    # ApiChannel init is sync, wrap it
    channel = ApiChannel(
        experiment_version,
        experiment_channel,
        experiment_session=session,
        user=user,
    )

    return await channel.anew_user_message(message)
