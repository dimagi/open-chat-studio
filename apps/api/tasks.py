from celery.app import shared_task
from celery.utils.log import get_task_logger
from django.db.models import Subquery

from apps.channels.clients.connect_client import CommCareConnectClient
from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chatbots.version_resolver import resolve_published_or_working
from apps.experiments.models import ExperimentSession, ParticipantData
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
    DEPRECATED: no longer queued; channels are created synchronously in the update-participant API
    view. Kept so in-flight tasks survive deploy. Remove in a follow-up.

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
    bot_name = channel.extra_data["commcare_connect_bot_name"]
    response = connect_client.create_channel(connect_id=connect_id, channel_source=bot_name)
    channel_id = response["channel_id"]

    # Connect's create_channel is idempotent on (connect_user, channel_source), so a reused bot
    # name returns a channel_id that may already be bound to another ParticipantData row. A
    # channel_id can only route to one experiment, so never store it on a second row.
    # See https://github.com/dimagi/open-chat-studio/issues/3620.
    existing = (
        ParticipantData.objects.filter(system_metadata__commcare_connect_channel_id=channel_id)
        .exclude(pk=participant_data.pk)
        .only("id", "experiment_id")
        .first()
    )
    if existing is not None:
        logger.error(
            "Connect returned channel_id %s for participant '%s' and experiment %s, but it is "
            "already bound to ParticipantData %s (experiment %s). Not storing it; the bot name "
            "'%s' was likely reused.",
            channel_id,
            connect_id,
            participant_data.experiment_id,
            existing.pk,
            existing.experiment_id,
            bot_name,
        )
        return

    participant_data.system_metadata = {
        "commcare_connect_channel_id": channel_id,
        "consent": response["consent"],
    }
    participant_data.save(update_fields=["system_metadata"])
    # Generate the key eagerly so the device (via generate_key) and the outbound send path can
    # never race to create different keys for the same channel.
    if not participant_data.encryption_key:
        participant_data.generate_encryption_key()


@shared_task(ignore_result=True)
def trigger_bot_message_task(session_external_id: str, prompt_text: str | None, message_text: str | None):
    """
    Trigger a bot message for a participant on a specific platform.

    The session must already exist (created synchronously by the view so that its ID can be
    returned to the caller before the task runs).

    When ``message_text`` is set, the message is delivered directly to the participant
    without any LLM processing. When ``prompt_text`` is set, the bot generates a
    response via the LLM and sends that.
    """
    session = ExperimentSession.objects.select_related("experiment", "experiment_channel", "participant").get(
        external_id=session_external_id
    )

    experiment = session.experiment
    target_experiment = resolve_published_or_working(experiment)

    with current_team(experiment.team):
        session.ad_hoc_bot_message(
            prompt_text,
            TraceInfo(name="api trigger"),
            fail_silently=False,
            use_experiment=target_experiment,
            message_text=message_text,
        )
