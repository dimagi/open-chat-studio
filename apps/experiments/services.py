from django.db import transaction
from django.db.models import Q

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.chat.exceptions import VersionedExperimentSessionsNotAllowedException
from apps.chat.models import Chat
from apps.events.models import StaticTriggerType
from apps.events.tasks import enqueue_static_triggers

from .models import Experiment, ExperimentSession, Participant, SessionStatus


def start_experiment_session(
    working_experiment: Experiment,
    experiment_channel: ExperimentChannel,
    participant: Participant,
    session_status: SessionStatus = SessionStatus.ACTIVE,
    timezone: str | None = None,
    session_external_id: str | None = None,
    metadata: dict | None = None,
) -> ExperimentSession:
    """Create a session for ``participant``, which wraps everything we know about the participant.

    ``participant`` need not be stored: callers that haven't resolved a record yet pass a plain
    wrapper (``Participant(identifier=...)`` or ``Participant(identifier=..., user=...)``) and it
    is looked up or created here. A stored participant (e.g. one the v2 pipeline already resolved)
    is used as-is.
    """
    if working_experiment.is_a_version:
        raise VersionedExperimentSessionsNotAllowedException(
            message="A session cannot be linked to an experiment version. "
        )

    team = working_experiment.team
    participant_identifier = participant.identifier
    participant_user = participant.user

    # Inline import to avoid circular import: channels_v2.stages.core imports from experiments.services
    from apps.channels.channels_v2.stages.core import get_or_create_participant  # noqa: PLC0415

    normalized_identifier = experiment_channel.platform_enum.normalize_identifier(participant_identifier)

    with transaction.atomic():
        # An unstored wrapper is resolved to (or created as) a real record here; a stored
        # participant (e.g. one the v2 pipeline already resolved) is used directly.
        if participant.pk is None:
            participant = get_or_create_participant(
                team=team,
                normalized_identifier=normalized_identifier,
                platform=experiment_channel.platform,
                participant_user=participant_user,
                participant_id_filter=Q(identifier=normalized_identifier),
            )

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
                "platform": experiment_channel.platform,
            },
        )

        # Record the participant's timezone
        if timezone:
            participant.update_memory(data={"timezone": timezone}, experiment=working_experiment)

    if experiment_channel.platform != ChannelPlatform.EVALUATIONS:
        if participant.experimentsession_set.filter(experiment=working_experiment).count() == 1:
            enqueue_static_triggers.delay(session.id, StaticTriggerType.PARTICIPANT_JOINED_EXPERIMENT)
        enqueue_static_triggers.delay(session.id, StaticTriggerType.CONVERSATION_START)
    return session
