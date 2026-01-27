from celery.app import shared_task
from celery.utils.log import get_task_logger
from django.db.models import Subquery

from apps.channels.clients.connect_client import CommCareConnectClient
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.channels import ChannelBase
from apps.experiments.models import Experiment, ParticipantData
from apps.service_providers.tracing import TraceInfo
from apps.teams.utils import current_team

logger = get_task_logger("ocs.api")


@shared_task(
    bind=True,
    acks_late=True,
    ignore_result=True,
    max_retries=3,
)
def setup_connect_channels_for_bots(self, connect_id: str, experiment_data_map: dict):
    """
    Set up Connect channels for experiments that are using the ConnectMessaging channel

    experiment_data_map: {experiment_id: participant_data_id}
    """

    experiment_ids = list(experiment_data_map.keys())
    participant_data_ids = list(experiment_data_map.values())

    # Only create channels for experiments that are using the ConnectMessaging channel
    experiments_using_connect = ExperimentChannel.objects.filter(
        platform=ChannelPlatform.COMMCARE_CONNECT,
        experiment__id__in=experiment_ids,
    ).values_list("experiment_id", flat=True)

    participant_data = (
        ParticipantData.objects.filter(
            id__in=participant_data_ids,
            experiment_id__in=Subquery(experiments_using_connect),
        )
        .exclude(system_metadata__has_key="commcare_connect_channel_id")
        .prefetch_related("experiment")
        .all()
    )

    connect_client = CommCareConnectClient()

    channels = ExperimentChannel.objects.filter(
        platform=ChannelPlatform.COMMCARE_CONNECT,
        experiment_id__in=[participant_data.experiment_id for participant_data in participant_data],
    )

    channels = {ch.experiment_id: ch for ch in channels}

    successful_ids = set()
    for participant_datum in participant_data:
        try:
            experiment = participant_datum.experiment
            channel = channels[experiment.id]
            create_connect_channel_for_participant(channel, connect_client, connect_id, participant_datum)
            successful_ids.add(experiment.id)
        except Exception as e:
            if self.request.retries == self.max_retries:
                failed_ids = set(experiment_ids) - successful_ids
                logger.exception(
                    "Failed to create channel for participant '%s' and experiments '{}'", connect_id, failed_ids
                )
            raise self.retry(exc=e, countdown=60) from None


def create_connect_channel_for_participant(channel, connect_client, connect_id, participant_data):
    response = connect_client.create_channel(
        connect_id=connect_id, channel_source=channel.extra_data["commcare_connect_bot_name"]
    )
    participant_data.system_metadata = {
        "commcare_connect_channel_id": response["channel_id"],
        "consent": response["consent"],
    }
    participant_data.save(update_fields=["system_metadata"])


@shared_task(ignore_result=True)
def trigger_bot_message_task(data):
    """
    Trigger a bot message for a participant on a specific platform using the prompt from the given data.
    """
    platform = data["platform"]
    experiment_public_id = data["experiment"]
    prompt_text = data["prompt_text"]
    identifier = data["identifier"]
    start_new_session = data["start_new_session"]
    session_data = data.get("session_data")

    experiment = Experiment.objects.get(public_id=experiment_public_id)
    experiment_channel = ExperimentChannel.objects.get(platform=platform, experiment=experiment)

    published_experiment = experiment.default_version
    ChannelClass = ChannelBase.get_channel_class_for_platform(platform)
    channel = ChannelClass(experiment=published_experiment, experiment_channel=experiment_channel)

    with current_team(experiment.team):
        channel.ensure_session_exists_for_participant(identifier, new_session=start_new_session)
        if session_data:
            session = channel.experiment_session
            merged_state = {**session.state, **session_data}
            session.state = merged_state
            session.save(update_fields=["state"])

        channel.experiment_session.ad_hoc_bot_message(
            prompt_text, TraceInfo(name="api trigger"), use_experiment=published_experiment
        )
